"""Celery task entry points: thin shared_task wrappers around core/ logic."""

import datetime as dt
import logging
from contextlib import contextmanager
from decimal import Decimal

from celery import shared_task
from django.core.cache import cache
from esi.exceptions import HTTPNotModified
from eveuniverse.models import EveMoon, EveSolarSystem, EveType

from whatax import providers
from whatax.core import matching, moons, tax
from whatax.core.config import get_config
from whatax.core.timeutils import eve_now, month_bounds, previous_month

logger = logging.getLogger(__name__)

_LOCK_TTL = 3600  # seconds; auto-expires so a crashed task can't wedge.
_RETRY = dict(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)


@contextmanager
def _lock(key: str):
    """Best-effort non-overlapping lock via cache.add."""
    acquired = cache.add(f"whatax:lock:{key}", "1", _LOCK_TTL)
    try:
        yield acquired
    finally:
        if acquired:
            cache.delete(f"whatax:lock:{key}")


def _results(operation, force_refresh: bool = False):
    """Run an ESI .results() call, treating a 304/ETag hit as "no change"."""
    try:
        return operation.results(force_refresh=force_refresh)
    except HTTPNotModified:
        return []


def _enabled() -> bool:
    return get_config().is_enabled


def _corp_token(corporation_id: int, scopes):
    """A valid token for any char in corporation_id holding scopes."""
    from allianceauth.eveonline.models import EveCharacter
    from esi.models import Token

    char_ids = list(
        EveCharacter.objects.filter(corporation_id=corporation_id).values_list(
            "character_id", flat=True
        )
    )
    return (
        Token.objects.filter(
            character_id__in=char_ids, whatax_registration__isnull=False
        )
        .require_scopes(scopes)
        .require_valid()
        .first()
    )


def _corps_with_token(scopes):
    """EVE corp IDs that have a valid token carrying scopes."""
    from allianceauth.eveonline.models import EveCharacter
    from esi.models import Token

    char_ids = list(
        Token.objects.filter(whatax_registration__isnull=False)
        .require_scopes(scopes)
        .require_valid()
        .values_list("character_id", flat=True)
    )
    return [
        cid
        for cid in set(
            EveCharacter.objects.filter(character_id__in=char_ids).values_list(
                "corporation_id", flat=True
            )
        )
        if cid
    ]


# --- ESI sync ---------------------------------------------------------------

# eveuniverse group id for Refinery structures (Athanor, Tatara) that drill moons.
_REFINERY_GROUP_ID = 1406


@shared_task(**_RETRY)
def sync_structures():
    """Refresh MiningStructure rows from the corp structures endpoint."""
    if not _enabled():
        return
    from allianceauth.eveonline.models import EveCorporationInfo
    from whatax.core.moons import is_sec_class_excluded
    from whatax.models import MiningStructure

    with _lock("sync_structures") as ok:
        if not ok:
            return
        config = get_config()
        scope = "esi-corporations.read_structures.v1"
        # Force refresh so a 304 can't make the prune below wipe every structure.
        seen_ids: set[int] = set()
        processed_corps: list[int] = []
        for corp_id in _corps_with_token(scope):
            token = _corp_token(corp_id, scope)
            if token is None:
                logger.warning("whatax: no structures token for corp %s", corp_id)
                continue
            corp = EveCorporationInfo.objects.filter(corporation_id=corp_id).first()
            # Gate on structure type so idle drills aren't pruned like the observers list would.
            processed_corps.append(corp_id)
            rows = _results(
                providers.esi.client.Corporation.GetCorporationsCorporationIdStructures(
                    corporation_id=corp_id, token=token
                ),
                force_refresh=True,
            )
            for row in rows:
                eve_type = _eve_type(getattr(row, "type_id", None))
                if eve_type is None or eve_type.eve_group_id != _REFINERY_GROUP_ID:
                    continue
                system = _eve_system(getattr(row, "system_id", None))
                # Skip out-of-scope sec class; the prune drops any tracked before.
                if is_sec_class_excluded(system, config):
                    continue
                MiningStructure.objects.update_or_create(
                    structure_id=row.structure_id,
                    defaults={
                        "corporation": corp,
                        "name": getattr(row, "name", "") or "",
                        "eve_type": eve_type,
                        "eve_solar_system": system,
                        "fuel_expires": getattr(row, "fuel_expires", None),
                    },
                )
                seen_ids.add(row.structure_id)
        # Drop structures no longer owned as refineries, scoped to corps reconciled this run.
        MiningStructure.objects.filter(
            corporation__corporation_id__in=processed_corps
        ).exclude(structure_id__in=seen_ids).delete()


@shared_task(**_RETRY)
def sync_mining_ledger():
    """Fan out one ledger-sync subtask per active structure."""
    if not _enabled():
        return
    from whatax.models import MiningStructure

    for sid in MiningStructure.objects.active().values_list("id", flat=True):
        sync_structure_ledger.delay(sid)


@shared_task(**_RETRY)
def sync_structure_ledger(structure_pk: int):
    """Land observer-ledger rows for one structure (upsert on natural key)."""
    if not _enabled():
        return
    from whatax.models import MiningLedgerEntry, MiningStructure

    structure = MiningStructure.objects.get(pk=structure_pk)
    token = _corp_token(structure.corporation.corporation_id, "esi-industry.read_corporation_mining.v1")
    if token is None:
        logger.warning("whatax: no mining token for structure %s", structure)
        return
    # force_refresh: per-observer requests share one ETag cache entry, so a
    # cached 304 would falsely empty all but the first structure; reconcile live.
    rows = _results(
        providers.esi.client.Industry.GetCorporationCorporationIdMiningObserversObserverId(
            corporation_id=structure.corporation.corporation_id,
            observer_id=structure.structure_id,
            token=token,
        ),
        force_refresh=True,
    )
    for row in rows:
        MiningLedgerEntry.objects.update_or_create(
            structure=structure,
            character_id=row.character_id,
            ore_type=_eve_type(row.type_id),
            recorded_date=row.last_updated,
            defaults={
                "quantity": row.quantity,
                "recorded_corporation_id": row.recorded_corporation_id,
            },
        )
    structure.last_ledger_sync = eve_now()
    structure.save(update_fields=["last_ledger_sync"])


@shared_task(**_RETRY)
def sync_moon_extractions():
    """Refresh the extraction schedule (chunk arrival / decay times)."""
    if not _enabled():
        return
    from whatax.models import MiningStructure, MoonExtraction

    with _lock("sync_moon_extractions") as ok:
        if not ok:
            return
        scope = "esi-industry.read_corporation_mining.v1"
        for corp_id in _corps_with_token(scope):
            token = _corp_token(corp_id, scope)
            if token is None:
                continue
            # force_refresh: rows with unsynced structures are skipped, so a cached ETag isn't trustworthy.
            rows = _results(
                providers.esi.client.Industry.GetCorporationCorporationIdMiningExtractions(
                    corporation_id=corp_id, token=token
                ),
                force_refresh=True,
            )
            touched = {}
            for row in rows:
                structure = MiningStructure.objects.filter(structure_id=row.structure_id).first()
                if structure is None:
                    continue
                MoonExtraction.objects.update_or_create(
                    structure=structure,
                    chunk_arrival_time=row.chunk_arrival_time,
                    defaults={
                        "eve_moon": _eve_moon(getattr(row, "moon_id", None)),
                        "extraction_start_time": row.extraction_start_time,
                        "natural_decay_time": getattr(row, "natural_decay_time", None),
                    },
                )
                touched[structure.pk] = structure
            # Schedule changed; reproject planned pop for these structures.
            for structure in touched.values():
                structure.recompute_planned_pop()


@shared_task(**_RETRY)
def poll_corp_notifications():
    """Read corp moon notifications and persist pop/composition events."""
    if not _enabled():
        return
    from whatax.models import MiningStructure, MoonExtraction

    with _lock("poll_corp_notifications") as ok:
        if not ok:
            return
        scope = "esi-characters.read_notifications.v1"
        for corp_id in _corps_with_token(scope):
            token = _corp_token(corp_id, scope)
            if token is None:
                continue
            notes = _results(
                providers.esi.client.Character.GetCharactersCharacterIdNotifications(
                    character_id=token.character_id, token=token
                )
            )
            # Apply oldest-first so a chunk's Started always precedes its pop.
            # Apply BEFORE claim: if apply raises (transient ESI error), the task
            # retries and re-applies rather than skipping permanently.  Apply is
            # idempotent (update_or_create), so re-running on retry is harmless.
            for note in sorted(notes, key=lambda n: n.timestamp):
                ntype = getattr(note, "type", None)
                if ntype == "MoonminingExtractionStarted":
                    if not _already_processed(note):
                        _apply_extraction_started(note)
                        _claim_notification(note)
                elif ntype in ("MoonminingLaserFired", "MoonminingAutomaticFracture"):
                    if not _already_processed(note):
                        _apply_pop(note)
                        _claim_notification(note)


def _already_processed(note) -> bool:
    """True if this notification was already handled (non-consuming check)."""
    from whatax.models import ProcessedNotification

    notification_id = getattr(note, "notification_id", None)
    if notification_id is None:
        return False
    return ProcessedNotification.objects.filter(notification_id=notification_id).exists()


def _claim_notification(note) -> bool:
    """Record an ESI notification as processed, returning True only the first time."""
    from django.db import IntegrityError, transaction

    from whatax.models import ProcessedNotification

    notification_id = getattr(note, "notification_id", None)
    if notification_id is None:
        return True
    try:
        with transaction.atomic():
            ProcessedNotification.objects.create(
                notification_id=notification_id,
                notification_type=getattr(note, "type", "") or "",
            )
    except IntegrityError:
        return False
    return True


def _apply_extraction_started(note):
    from whatax.models import ExtractionOre, MiningStructure, MoonExtraction
    from whatax.notifications import notify_moon_pop  # noqa: F401

    parsed = moons.parse_extraction_started(note.text)
    structure = MiningStructure.objects.filter(structure_id=parsed["structure_id"]).first()
    if structure is None or parsed["chunk_arrival_time"] is None:
        return
    defaults = {
        "eve_moon": _eve_moon(parsed["moon_id"]),
        "extraction_start_time": note.timestamp,
        "natural_decay_time": parsed["natural_decay_time"],
    }
    # Never resurrect a terminal extraction to ACTIVE on an out-of-order replay.
    existing = MoonExtraction.objects.filter(
        structure=structure, chunk_arrival_time=parsed["chunk_arrival_time"]
    ).first()
    if existing is None or existing.status not in MoonExtraction.TERMINAL_STATUSES:
        defaults["status"] = MoonExtraction.Status.ACTIVE
    extraction, _ = MoonExtraction.objects.update_or_create(
        structure=structure,
        chunk_arrival_time=parsed["chunk_arrival_time"],
        defaults=defaults,
    )
    good_ore_ids = moons.good_ore_ids_for(structure)
    total = Decimal("0")
    for type_id, volume in parsed["ore_volume_by_type"].items():
        is_good = type_id in good_ore_ids
        ExtractionOre.objects.update_or_create(
            extraction=extraction,
            ore_type=_eve_type(type_id),
            defaults={"volume_m3": volume, "is_good_ore": is_good},
        )
        if is_good:
            total += volume
    # No good ore => leave NULL (unknown) rather than a 0 denominator that wedges dead-detection.
    extraction.total_good_ore_m3 = total if total > 0 else None
    extraction.save(update_fields=["total_good_ore_m3"])
    structure.recompute_planned_pop()


def _apply_pop(note):
    import yaml

    from whatax.models import MoonExtraction
    from whatax.notifications import notify_moon_pop

    data = yaml.safe_load(note.text) or {}
    structure_id = data.get("structureID")
    # Pop the latest already-arrived chunk, not a still-future scheduled one;
    # fall back to the earliest non-popped row when none has a recorded arrival.
    candidates = MoonExtraction.objects.filter(
        structure__structure_id=structure_id
    ).exclude(status=MoonExtraction.Status.POPPED)
    extraction = (
        candidates.filter(chunk_arrival_time__lte=note.timestamp)
        .order_by("-chunk_arrival_time")
        .first()
        or candidates.order_by("chunk_arrival_time").first()
    )
    if extraction is None:
        return
    extraction.status = MoonExtraction.Status.POPPED
    extraction.popped_at = note.timestamp
    extraction.save(update_fields=["status", "popped_at"])
    # Reproject from whatever's now soonest after this pop.
    extraction.structure.recompute_planned_pop()
    notify_moon_pop(extraction)


@shared_task(**_RETRY)
def sweep_structure_health():
    """One hourly health pass: recompute dead % + DM staff on low fuel / off-schedule drift.

    Recomputes moon dead status for all non-terminal extractions and notifies on
    transition, then sweeps active structures for low-fuel and pop-drift conditions,
    all under one lock so both steps run atomically in the same cycle.
    """
    if not _enabled():
        return
    from whatax.models import MiningStructure, MoonExtraction
    from whatax.notifications import notify_moon_dead, reconcile_low_fuel, reconcile_pop_drift

    config = get_config()
    with _lock("sweep_structure_health") as ok:
        if not ok:
            return

        # 1. Recompute dead % for non-terminal extractions and notify on transition.
        actives = MoonExtraction.objects.exclude(
            status__in=[MoonExtraction.Status.DEAD, MoonExtraction.Status.CANCELLED]
        )
        for extraction in actives:
            if moons.recompute_dead(extraction):
                notify_moon_dead(extraction)

        # 2. Sweep active structures for low-fuel reminders and off-schedule pop alerts.
        for structure in MiningStructure.objects.active().select_related("group", "eve_solar_system"):
            reconcile_low_fuel(structure, config)
            reconcile_pop_drift(structure)


@shared_task(**_RETRY)
def sync_and_reconcile_payments():
    """Land corp wallet journal rows, then match fresh inflows to outstanding bills.

    The wallet-journal sync and payment reconciliation run back-to-back under one
    lock so freshly-landed inflows are matched in the same cycle, with no
    cross-task scheduling to keep in sync.
    """
    if not _enabled():
        return
    from whatax.models import Payment, WalletJournalEntry
    from whatax.notifications import notify_payment_received

    config = get_config()
    with _lock("sync_and_reconcile_payments") as ok:
        if not ok:
            return

        # 1. Land the corp wallet journal for the configured division (upsert on id).
        if config.payment_corporation_id:
            corp_id = config.payment_corporation.corporation_id
            division = config.payment_wallet_division
            token = _corp_token(corp_id, "esi-wallet.read_corporation_wallets.v1")
            if token is None:
                logger.warning("whatax: no wallet token for corp %s", corp_id)
            else:
                rows = _results(
                    providers.esi.client.Wallet.GetCorporationsCorporationIdWalletsDivisionJournal(
                        corporation_id=corp_id, division=division, token=token
                    )
                )
                for row in rows:
                    WalletJournalEntry.objects.update_or_create(
                        entry_id=row.id,
                        defaults={
                            "division": division,
                            "ref_type": getattr(row, "ref_type", "") or "",
                            "amount": getattr(row, "amount", None) or 0,
                            "balance": getattr(row, "balance", None),
                            "date": row.date,
                            "first_party_id": getattr(row, "first_party_id", None),
                            "second_party_id": getattr(row, "second_party_id", None),
                            "reason": getattr(row, "reason", "") or "",
                        },
                    )

        # 2. Match unprocessed inflows to outstanding bills and notify payers.
        matching.reconcile_payments()
        for payment in Payment.objects.filter(notified_at__isnull=True).exclude(
            tax_record__isnull=True
        ):
            notify_payment_received(payment)


@shared_task(**_RETRY)
def run_monthly_tax(
    year: int | None = None, month: int | None = None, *, force: bool = False
):
    """Emit the previous month's bills on the 1st; idempotent, force=True re-runs a finalized period."""
    if not _enabled():
        return
    from whatax.models import TaxPeriod
    from whatax.notifications import notify_tax_due

    if year is None or month is None:
        year, month = previous_month()

    with _lock("run_monthly_tax") as ok:
        if not ok:
            return
        period = TaxPeriod.objects.filter(year=year, month=month).first()
        if not force and period and period.state == TaxPeriod.State.FINALIZED:
            logger.info("whatax: period %s-%02d already finalized; skipping", year, month)
            return
        if period is None:
            start, end = month_bounds(year, month)
            period = TaxPeriod.objects.create(
                year=year, month=month, period_start=start, period_end=end
            )
        tax.calculate_period(period)
        for record in period.tax_records.filter(notified_due_at__isnull=True):
            notify_tax_due(record)


# --- eveuniverse resolution helpers -----------------------------------------


def _eve_type(type_id):
    if not type_id:
        return None
    # Load TYPE_MATERIALS so ore has a reprocessing recipe for pricing; fetched once on creation.
    obj, _ = EveType.objects.get_or_create_esi(
        id=type_id, enabled_sections=[EveType.Section.TYPE_MATERIALS]
    )
    return obj


def _eve_system(system_id):
    if not system_id:
        return None
    obj, _ = EveSolarSystem.objects.get_or_create_esi(id=system_id)
    return obj


def _eve_moon(moon_id):
    if not moon_id:
        return None
    obj, _ = EveMoon.objects.get_or_create_esi(id=moon_id)
    return obj
