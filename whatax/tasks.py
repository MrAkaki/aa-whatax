"""Celery task entry points (TECHNICAL.md §8, §10, §13).

Thin ``@shared_task`` wrappers around ``core/`` logic. Every scheduled task
short-circuits when ``TaxConfiguration.is_enabled`` is False and acquires a
Django-cache lock to prevent overlapping runs of the same sync (§13). ESI
fan-out uses per-structure subtasks with ``autoretry_for`` / ``retry_backoff`` so
one slow/failing structure is isolated (§6.3).

ESI response field names below use the stable public-ESI names; verify against
the live schema in your env when first wiring this up.
"""

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
from whatax.core.timeutils import eve_now, month_bounds, previous_month

logger = logging.getLogger(__name__)

_LOCK_TTL = 3600  # seconds; a lock auto-expires so a crashed task can't wedge.
_RETRY = dict(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)


@contextmanager
def _lock(key: str):
    """Best-effort non-overlapping lock via cache.add (atomic on most backends)."""
    acquired = cache.add(f"whatax:lock:{key}", "1", _LOCK_TTL)
    try:
        yield acquired
    finally:
        if acquired:
            cache.delete(f"whatax:lock:{key}")


def _results(operation, force_refresh: bool = False):
    """Run an ESI ``.results()`` call, treating a 304/ETag hit as "no change".

    django-esi raises :class:`~esi.exceptions.HTTPNotModified` when every page
    matched its stored ETag, i.e. nothing changed since the last successful
    sync. That is the normal ETag fast-path (§6.3), not a failure, so we return
    an empty result set and let the caller's upsert loop no-op — the rows we
    already persisted are still current. Without this the bare exception bubbles
    through ``autoretry_for=(Exception,)`` and every unchanged sync retries five
    times before logging a spurious ``HTTPError`` (the worker error in the logs).

    The 304 fast-path is only safe when *every* 200 response is fully persisted,
    so a later 304 ("unchanged") still describes what's in the DB. Callers that
    may skip rows from a corp-wide response (e.g. extraction rows whose structure
    isn't synced yet) must pass ``force_refresh=True``: skipping a row while the
    ETag is cached as "seen" would otherwise hide that data permanently — every
    subsequent run gets a 304 and never re-attempts the skipped rows until the
    server-side body changes.
    """
    try:
        return operation.results(force_refresh=force_refresh)
    except HTTPNotModified:
        return []


def _config():
    from whatax.models import TaxConfiguration

    return TaxConfiguration.objects.get_solo()


def _enabled() -> bool:
    return _config().is_enabled


def _corp_token(corporation_id: int, scopes):
    """A valid token for any char in ``corporation_id`` holding ``scopes``."""
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
    """EVE corp IDs that have a valid token carrying ``scopes``.

    Drives the structure/moon/notification syncs: we only hit a corp's ESI
    endpoints when a char in that corp has granted the token those reads need.
    This bootstraps a structures corp the moment its director token is added and,
    crucially, never tries to read structures from the wallet-only payment corp,
    whose Accountant token lacks the structures scope (the source of the spurious
    "no structures token for corp ..." warning).
    """
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


@shared_task(**_RETRY)
def sync_structures():
    """Refresh ``MiningStructure`` rows from the corp structures endpoint (§6.2)."""
    if not _enabled():
        return
    from allianceauth.eveonline.models import EveCorporationInfo
    from whatax.core.moons import is_sec_class_excluded
    from whatax.models import MiningStructure

    with _lock("sync_structures") as ok:
        if not ok:
            return
        config = _config()
        scope = "esi-corporations.read_structures.v1"
        # Reconciliation needs the live, complete picture: a 304 would make
        # _results() return [] and the prune below would wrongly delete every
        # structure, so both reads force_refresh (§6.3, _results docstring).
        seen_ids: set[int] = set()
        processed_corps: list[int] = []
        for corp_id in _corps_with_token(scope):
            token = _corp_token(corp_id, scope)
            if token is None:
                logger.warning("whatax: no structures token for corp %s", corp_id)
                continue
            corp = EveCorporationInfo.objects.filter(corporation_id=corp_id).first()
            # The mining-observers list is the authoritative set of moon-drilling
            # refineries. The corp structures endpoint also returns citadels,
            # engineering complexes, etc., so we keep only structures that appear
            # here. The structures token bundle carries the mining scope too, so
            # the same token reads both (§6.1/§6.2).
            observers = _results(
                providers.esi.client.Industry.GetCorporationCorporationIdMiningObservers(
                    corporation_id=corp_id, token=token
                ),
                force_refresh=True,
            )
            observer_ids = {
                o.observer_id
                for o in observers
                if getattr(o, "observer_type", None) == "structure"
            }
            processed_corps.append(corp_id)
            rows = _results(
                providers.esi.client.Corporation.GetCorporationsCorporationIdStructures(
                    corporation_id=corp_id, token=token
                ),
                force_refresh=True,
            )
            for row in rows:
                if row.structure_id not in observer_ids:
                    continue
                eve_type = _eve_type(getattr(row, "type_id", None))
                system = _eve_system(getattr(row, "system_id", None))
                # Out-of-scope sec class (e.g. low/null when only HS is taxed):
                # don't track at all — the prune below drops any that slipped in
                # before, since it isn't added to seen_ids.
                if is_sec_class_excluded(system, config):
                    continue
                MiningStructure.objects.update_or_create(
                    structure_id=row.structure_id,
                    defaults={
                        "corporation": corp,
                        "name": getattr(row, "name", "") or "",
                        "eve_type": eve_type,
                        "eve_solar_system": system,
                    },
                )
                seen_ids.add(row.structure_id)
        # Drop structures that are no longer mining observers (incl. non-refinery
        # rows left by earlier syncs). Scoped to corps we actually reconciled this
        # run, so a corp whose token is missing keeps its structures untouched.
        MiningStructure.objects.filter(
            corporation__corporation_id__in=processed_corps
        ).exclude(structure_id__in=seen_ids).delete()


@shared_task(**_RETRY)
def sync_mining_ledger():
    """Fan out one ledger-sync subtask per active structure (§6.3 fan-out)."""
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
    # force_refresh: django-esi caches the ETag under the *path template*
    # (/corporation/{corporation_id}/mining/observers/{observer_id}), not the
    # resolved observer_id, so every structure in this per-observer fan-out
    # collides on one shared cache entry. The first structure synced stores an
    # ETag and every later structure gets a false 304 -> _results() returns []
    # -> no rows land, yet last_ledger_sync below still stamps it "done". That
    # silently hides all but one structure's mining forever (the body rarely
    # changes for an idle observer). Always reconcile live, like sync_structures
    # and sync_moon_extractions (§6.3, see _results docstring).
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
            # force_refresh: this corp-wide response is upserted row-by-row and
            # rows whose structure isn't synced yet are skipped, so a cached ETag
            # can't be trusted to mean "fully persisted" — always reconcile live.
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
            # The schedule changed for these structures; reproject their planned pop.
            for structure in touched.values():
                structure.recompute_planned_pop()


@shared_task(**_RETRY)
def poll_corp_notifications():
    """Read corp moon notifications and persist pop/composition events (§11)."""
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
            for note in notes:
                ntype = getattr(note, "type", None)
                if ntype == "MoonminingExtractionStarted":
                    if _claim_notification(note):
                        _apply_extraction_started(note)
                elif ntype in ("MoonminingLaserFired", "MoonminingAutomaticFracture"):
                    if _claim_notification(note):
                        _apply_pop(note)


def _claim_notification(note) -> bool:
    """Record an ESI notification as processed, returning True only the first time.

    ESI replays a rolling window of notifications on every poll, and the same
    event may even arrive via multiple corp characters' tokens. Persisting the
    stable ``notification_id`` behind a unique constraint makes this the single
    chokepoint that turns each event into exactly one apply/notify: a duplicate
    (or a concurrent double-poll) hits the constraint and returns False instead
    of re-popping the next extraction and re-pinging Discord (the duplicate
    moon-pop notification bug). A notification without an id (e.g. a hand-built
    test stub) is always applied — dedup is a best-effort guard, never a gate
    that would silently drop real events.
    """
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
    from whatax.notifications import notify_moon_pop  # noqa: F401 (kept local for clarity)

    parsed = moons.parse_extraction_started(note.text)
    structure = MiningStructure.objects.filter(structure_id=parsed["structure_id"]).first()
    if structure is None or parsed["chunk_arrival_time"] is None:
        return
    extraction, _ = MoonExtraction.objects.update_or_create(
        structure=structure,
        chunk_arrival_time=parsed["chunk_arrival_time"],
        defaults={
            "eve_moon": _eve_moon(parsed["moon_id"]),
            "extraction_start_time": note.timestamp,
            "natural_decay_time": parsed["natural_decay_time"],
            "status": MoonExtraction.Status.ACTIVE,
        },
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
    # No good ore in the chunk => composition effectively unknown for dead-detection;
    # leave NULL rather than a fabricated 0 denominator that wedges at "popped" (§19).
    extraction.total_good_ore_m3 = total if total > 0 else None
    extraction.save(update_fields=["total_good_ore_m3"])
    structure.recompute_planned_pop()


def _apply_pop(note):
    import yaml

    from whatax.models import MoonExtraction
    from whatax.notifications import notify_moon_pop

    data = yaml.safe_load(note.text) or {}
    structure_id = data.get("structureID")
    # A laser-fired / auto-fracture event pops the chunk that has *arrived*, i.e.
    # the extraction whose chunk_arrival_time is at/before the notification time —
    # the most recent such arrival. The previous ``order_by("-chunk_arrival_time")``
    # picked the chunk with the *largest* arrival time, which (whenever a later
    # cycle is already SCHEDULED, as the corp-extractions sync routinely records)
    # is a still-future extraction. Popping that future row marked the upcoming
    # cycle POPPED, so the Staff dashboard showed "none scheduled" for a moon that
    # in fact had a pop coming (§11/§15.2). Constrain to arrived chunks and take
    # the latest, falling back to the earliest non-popped row if none has a
    # recorded arrival yet (composition/notification gaps).
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
    # This pop closed the next-pop row; reproject from whatever's now soonest.
    extraction.structure.recompute_planned_pop()
    notify_moon_pop(extraction)


@shared_task(**_RETRY)
def update_moon_status():
    """Recompute dead % for active/popped extractions and notify on transition."""
    if not _enabled():
        return
    from whatax.models import MoonExtraction
    from whatax.notifications import notify_moon_dead

    config = _config()
    actives = MoonExtraction.objects.exclude(
        status__in=[MoonExtraction.Status.DEAD, MoonExtraction.Status.CANCELLED]
    )
    for extraction in actives:
        if moons.recompute_dead(extraction):
            notify_moon_dead(extraction)


@shared_task(**_RETRY)
def sync_wallet_journal():
    """Land corp wallet journal rows for the configured division (upsert on id)."""
    if not _enabled():
        return
    from whatax.models import WalletJournalEntry

    config = _config()
    if not config.payment_corporation_id:
        return
    corp_id = config.payment_corporation.corporation_id
    division = config.payment_wallet_division
    token = _corp_token(corp_id, "esi-wallet.read_corporation_wallets.v1")
    if token is None:
        logger.warning("whatax: no wallet token for corp %s", corp_id)
        return
    with _lock("sync_wallet_journal") as ok:
        if not ok:
            return
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


@shared_task(**_RETRY)
def reconcile_payments():
    """Match unprocessed wallet inflows to outstanding bills (§10)."""
    if not _enabled():
        return
    from whatax.models import Payment
    from whatax.notifications import notify_payment_received

    with _lock("reconcile_payments") as ok:
        if not ok:
            return
        matching.reconcile_payments()
        for payment in Payment.objects.filter(notified_at__isnull=True).exclude(
            tax_record__isnull=True
        ):
            notify_payment_received(payment)


@shared_task(**_RETRY)
def run_monthly_tax(
    year: int | None = None, month: int | None = None, *, force: bool = False
):
    """Emit the previous month's bills on the 1st (idempotent / self-correcting §13).

    The scheduled beat skips an already-``FINALIZED`` period so it never auto
    re-emits/re-notifies. An explicit admin re-run passes ``force=True`` to
    recompute regardless: ``calculate_period`` is idempotent (preserves
    ``amount_paid``, payment links and staff edits), so this is how an empty or
    stale finalized period — e.g. one calculated before its mining ledger had
    synced — gets repaired.
    """
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
        # One final ledger pass would normally precede calc; assume recent sync.
        tax.calculate_period(period)
        for record in period.tax_records.filter(notified_due_at__isnull=True):
            notify_tax_due(record)


# --- eveuniverse resolution helpers -----------------------------------------


def _eve_type(type_id):
    if not type_id:
        return None
    # Load the reprocessing recipe (TYPE_MATERIALS) alongside the type: pricing
    # values ore by the minerals it refines into (core.pricing), and a type
    # created without that section has no EveTypeMaterial rows, so calc fails
    # loud ("no reprocessing materials for ore type …") and rolls back the whole
    # period. Moon ores in particular are only ever first seen here, so without
    # this their recipes never load. enabled_sections only fetches on creation,
    # so this is a one-time ESI cost per new type.
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
