"""Ledger -> per-player snapshot aggregation (TECHNICAL.md §8).

Resolve each ledger row to its **player** (AA ``User``) via
``character_id -> EveCharacter -> CharacterOwnership.user``, group by
``user`` + ``ore_type`` (+ excluded bucket) for the period and sum ``quantity``
into ``MiningSnapshot`` rows. Idempotent: ledger rows are upserted on their
natural key, so re-aggregation is safe to repeat.

Edge cases (never silently drop ISK, §8/§19):
- unowned character -> ``UNATTRIBUTED`` sentinel user, surfaced in the Staff tab;
- no main set -> default rate at calc time, flagged;
- ownership changed mid-month -> resolved as of the run; corp frozen on the bill.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from allianceauth.authentication.models import CharacterOwnership
from django.contrib.auth.models import User

from whatax.core.moons import is_structure_excluded

logger = logging.getLogger(__name__)

UNATTRIBUTED_USERNAME = "whatax_unattributed"


def unattributed_user() -> User:
    """The sentinel player that holds mining we can't attribute (§8).

    A real (but inactive, unusable-password) ``auth.User`` so the non-null
    ``PROTECT`` FK on snapshots/records stays satisfied without inventing a
    nullable column.
    """
    user, created = User.objects.get_or_create(
        username=UNATTRIBUTED_USERNAME,
        defaults={"is_active": False, "first_name": "UNATTRIBUTED"},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


@dataclass
class PlayerResolution:
    user: User
    main_corporation_id: int | None
    has_main: bool


def resolve_player(character_id: int) -> PlayerResolution:
    """Resolve a mining character_id to its player and rate-determining corp."""
    ownership = (
        CharacterOwnership.objects.filter(character__character_id=character_id)
        .select_related("user__profile__main_character")
        .first()
    )
    if ownership is None:
        return PlayerResolution(unattributed_user(), None, False)

    main = getattr(getattr(ownership.user, "profile", None), "main_character", None)
    if main is None:
        return PlayerResolution(ownership.user, None, False)
    return PlayerResolution(ownership.user, main.corporation_id, True)


def _resolve_miner_labels(character_ids):
    """Batch-resolve a set of character_ids to display info.

    Returns a dict ``character_id -> (label, is_registered, user_id)``:
    - registered (has a ``CharacterOwnership``): label is the player's name,
      ``user_id`` is set so all of a player's characters roll up into one row;
    - unregistered: label is the character name (resolved via ``EveCharacter``
      then eveuniverse ``EveEntity``, falling back to the raw id), ``user_id``
      is ``None`` and the row is flagged for red rendering.
    """
    from allianceauth.eveonline.models import EveCharacter

    character_ids = set(character_ids)
    out: dict[int, tuple[str, bool, int | None]] = {}

    # One query: every ownership whose character_id is in the set -> its user.
    owned = (
        CharacterOwnership.objects.filter(character__character_id__in=character_ids)
        .select_related("user", "character")
    )
    user_label: dict[int, str] = {}
    for ownership in owned:
        cid = ownership.character.character_id
        uid = ownership.user_id
        if uid not in user_label:
            user_label[uid] = _player_label(ownership.user)
        out[cid] = (user_label[uid], True, uid)

    # Remaining ids are unregistered — resolve their character names.
    unregistered = character_ids - set(out)
    if unregistered:
        names = dict(
            EveCharacter.objects.filter(character_id__in=unregistered).values_list(
                "character_id", "character_name"
            )
        )
        for cid in unregistered:
            name = names.get(cid)
            if not name:
                name = _resolve_entity_name(cid)
            out[cid] = (name, False, None)
    return out


def _player_label(user) -> str:
    """A readable player name: main character if set, else the username."""
    main = getattr(getattr(user, "profile", None), "main_character", None)
    if main is not None:
        return main.character_name
    return user.username


def _resolve_entity_name(character_id) -> str:
    """Resolve an unregistered character name via eveuniverse, never crashing."""
    try:
        from eveuniverse.models import EveEntity

        entity, _ = EveEntity.objects.get_or_create_esi(id=character_id)
        if entity and entity.name:
            return entity.name
    except Exception:  # pragma: no cover - ESI/network failure is non-fatal
        logger.warning("Could not resolve name for character_id=%s", character_id)
    return str(character_id)


def monthly_mining_rows(year: int, month: int) -> list[dict]:
    """Per (miner, structure, ore) summed-units rows for a calendar month (§15.2).

    Registered characters roll up under their player; unregistered character_ids
    each get their own row flagged ``is_registered=False`` (red in the UI).
    """
    from whatax.models import MiningLedgerEntry

    entries = MiningLedgerEntry.objects.filter(
        recorded_date__year=year, recorded_date__month=month
    ).select_related("structure", "ore_type")

    labels = _resolve_miner_labels(entries.values_list("character_id", flat=True))

    # Group key: (miner identity, structure_id, ore_type_id). Registered miners
    # key on user_id so multiple characters merge; unregistered key on char id.
    totals: dict[tuple, int] = defaultdict(int)
    meta: dict[tuple, dict] = {}
    for entry in entries:
        label, is_registered, user_id = labels[entry.character_id]
        miner_key = ("user", user_id) if is_registered else ("char", entry.character_id)
        key = (miner_key, entry.structure_id, entry.ore_type_id)
        totals[key] += entry.quantity
        if key not in meta:
            meta[key] = {
                "miner_label": label,
                "is_registered": is_registered,
                "structure": str(entry.structure),
                "ore": entry.ore_type.name,
            }

    rows = []
    for key, units in totals.items():
        row = dict(meta[key])
        row["units"] = units
        rows.append(row)

    rows.sort(
        key=lambda r: (r["miner_label"].lower(), r["structure"].lower(), r["ore"].lower())
    )
    return rows


def unregistered_character_rows(period, config=None) -> list[dict]:
    """Per unregistered character: refined value & tax for the period (§8/§15.2).

    Unregistered mining is stored aggregated under the `whatax_unattributed`
    sentinel; here we re-derive a per-character breakdown for display. Unit
    refined value per ore is taken from the sentinel's snapshots (so it matches
    the figures used at calc time) and applied to each character's ledger
    quantities. Tax mirrors calculate_period: only non-excluded mining is taxed
    at the default rate.
    """
    from whatax.models import MiningLedgerEntry, TaxConfiguration

    config = config or TaxConfiguration.objects.get_solo()
    sentinel = unattributed_user()

    # Per (ore_type_id, is_excluded) unit refined value, from the sentinel's
    # snapshots — the source of truth for what was priced at calc time.
    unit_price: dict[tuple[int, bool], Decimal] = {}
    for snap in period.snapshots.filter(user=sentinel):
        if snap.quantity:
            unit_price[(snap.ore_type_id, snap.is_excluded)] = (
                snap.refined_value / snap.quantity
            )

    entries = MiningLedgerEntry.objects.filter(
        recorded_date__year=period.year, recorded_date__month=period.month
    ).select_related("structure", "ore_type")

    labels = _resolve_miner_labels(entries.values_list("character_id", flat=True))

    excluded_cache: dict[int, bool] = {}
    refined: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    taxable: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    seen: dict[int, str] = {}

    for entry in entries:
        label, is_registered, _user_id = labels[entry.character_id]
        if is_registered:
            continue
        struct_id = entry.structure_id
        if struct_id not in excluded_cache:
            excluded_cache[struct_id] = is_structure_excluded(entry.structure, config)
        excluded = excluded_cache[struct_id]
        price = unit_price.get((entry.ore_type_id, excluded), Decimal("0"))
        value = entry.quantity * price
        refined[entry.character_id] += value
        if not excluded:
            taxable[entry.character_id] += value
        seen.setdefault(entry.character_id, label)

    rate = config.default_tax_rate
    cent = Decimal("0.01")
    rows = []
    for character_id, label in seen.items():
        # Round to 2 decimals (cents) like every stored money field, so the rows
        # — and the per-table totals summed from them — never show more precision.
        refined_value = refined[character_id].quantize(cent, rounding=ROUND_HALF_UP)
        taxable_value = taxable[character_id].quantize(cent, rounding=ROUND_HALF_UP)
        tax_due = (taxable_value * rate).quantize(cent, rounding=ROUND_HALF_UP)
        rows.append(
            {
                "character_id": character_id,
                "label": label,
                "refined_value": refined_value,
                "taxable_value": taxable_value,
                "tax_due": tax_due,
            }
        )
    rows.sort(key=lambda r: r["label"].lower())
    return rows


def unregistered_character_breakdown(period, character_id, config=None) -> dict:
    """Per-(structure, ore) mining drill-down for one unregistered character (§15.2).

    The single-character analogue of ``unregistered_character_rows``: it reuses the
    same source of truth (unit refined value from the sentinel's snapshots, applied
    to the character's raw ledger quantities) but breaks the character's mining out
    into one row per (structure, ore) with summed quantity, refined value, whether
    that structure is excluded, and tax due. Tax mirrors ``calculate_period``: only
    non-excluded mining is taxed at the default rate. ISK is rounded to cents with
    ROUND_HALF_UP like every stored money field.

    Returns ``{"label", "is_registered", "rows", "total_refined", "total_tax"}``.
    """
    from whatax.models import MiningLedgerEntry, TaxConfiguration

    config = config or TaxConfiguration.objects.get_solo()
    sentinel = unattributed_user()

    # Per (ore_type_id, is_excluded) unit refined value, from the sentinel's
    # snapshots — same calc-time figures used by unregistered_character_rows.
    unit_price: dict[tuple[int, bool], Decimal] = {}
    for snap in period.snapshots.filter(user=sentinel):
        if snap.quantity:
            unit_price[(snap.ore_type_id, snap.is_excluded)] = (
                snap.refined_value / snap.quantity
            )

    entries = MiningLedgerEntry.objects.filter(
        recorded_date__year=period.year,
        recorded_date__month=period.month,
        character_id=character_id,
    ).select_related("structure", "ore_type")

    label, is_registered, _user_id = _resolve_miner_labels([character_id])[character_id]

    excluded_cache: dict[int, bool] = {}
    grouped: dict[tuple[int, int], int] = defaultdict(int)
    meta: dict[tuple[int, int], dict] = {}
    for entry in entries:
        struct_id = entry.structure_id
        if struct_id not in excluded_cache:
            excluded_cache[struct_id] = is_structure_excluded(entry.structure, config)
        key = (struct_id, entry.ore_type_id)
        grouped[key] += entry.quantity
        if key not in meta:
            meta[key] = {
                "structure": str(entry.structure),
                "ore": entry.ore_type.name,
                "excluded": excluded_cache[struct_id],
            }

    rate = config.default_tax_rate
    cent = Decimal("0.01")
    rows = []
    total_refined = Decimal("0")
    total_tax = Decimal("0")
    for key, units in grouped.items():
        info = meta[key]
        excluded = info["excluded"]
        price = unit_price.get((key[1], excluded), Decimal("0"))
        refined_value = (units * price).quantize(cent, rounding=ROUND_HALF_UP)
        tax_due = (
            Decimal("0")
            if excluded
            else (units * price * rate).quantize(cent, rounding=ROUND_HALF_UP)
        )
        total_refined += refined_value
        total_tax += tax_due
        rows.append(
            {
                "structure": info["structure"],
                "ore": info["ore"],
                "quantity": units,
                "refined_value": refined_value,
                "excluded": excluded,
                "tax_due": tax_due,
            }
        )
    rows.sort(key=lambda r: (r["structure"].lower(), r["ore"].lower()))

    return {
        "label": label,
        "is_registered": is_registered,
        "rows": rows,
        "total_refined": total_refined,
        "total_tax": total_tax,
    }


def player_ore_breakdown(period, user) -> dict:
    """Per-(character, ore) mining pivot for one player in one period (§8/§15.2).

    Builds the data behind the per-player drill-down on ``period_detail``: a pivot
    with one column per distinct ore the player mined this period and one row per
    of the player's individual characters, the cell being that character's summed
    quantity of that ore from the raw ledger. See ``player_ore_breakdown_for_month``
    for the returned shape; this is the ``TaxPeriod``-keyed wrapper used by staff.
    """
    return player_ore_breakdown_for_month(user, period.year, period.month)


def player_ore_breakdown_for_month(user, year: int, month: int) -> dict:
    """Per-(character, ore) mining pivot for one player in a calendar month (§15.1).

    The user-facing form (own dashboard) and shared engine behind
    ``player_ore_breakdown``: the units pivot is built straight from the raw
    ledger, so it works for the open, not-yet-calculated month. Each ore column's
    ISK value is the column's total units × the player's refined **unit** price
    from that month's ``MiningSnapshot`` rows (excluded + non-excluded buckets
    summed, matching the figures used at calc time); it is 0 before the month is
    calculated or for any ore lacking a snapshot. ISK is rounded to cents with
    ROUND_HALF_UP like every stored money field.

    Returns a dict (cell/total lists are column-aligned to ``ores`` so templates
    can iterate without dict-by-key lookups):
    - ``ores``: ordered list of distinct ore names mined this month;
    - ``characters``: ``[{"label", "cells": [qty, ...]}]``, one per character;
    - ``totals_units``: ``[qty, ...]`` summed across the player's characters;
    - ``totals_isk``: ``[Decimal, ...]`` refined ISK value of each ore's total.
    """
    from whatax.models import MiningLedgerEntry, TaxPeriod

    character_ids = list(
        CharacterOwnership.objects.filter(user=user).values_list(
            "character__character_id", "character__character_name"
        )
    )
    char_name: dict[int, str] = {cid: name for cid, name in character_ids}
    ids = set(char_name)

    entries = MiningLedgerEntry.objects.filter(
        recorded_date__year=year,
        recorded_date__month=month,
        character_id__in=ids,
    ).select_related("ore_type")

    # (character_id, ore_type_id) -> summed quantity; track ore names + totals.
    cells: dict[tuple[int, int], int] = defaultdict(int)
    totals_units_by_id: dict[int, int] = defaultdict(int)
    ore_name: dict[int, str] = {}
    for entry in entries:
        cells[(entry.character_id, entry.ore_type_id)] += entry.quantity
        totals_units_by_id[entry.ore_type_id] += entry.quantity
        ore_name.setdefault(entry.ore_type_id, entry.ore_type.name)

    # Per ore_type_id refined unit price from this user's snapshots (both buckets
    # summed), when the month has a (calculated) period; 0 otherwise.
    snap_qty: dict[int, int] = defaultdict(int)
    snap_value: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    period = TaxPeriod.objects.filter(year=year, month=month).first()
    if period is not None:
        for snap in period.snapshots.filter(user=user):
            snap_qty[snap.ore_type_id] += snap.quantity
            snap_value[snap.ore_type_id] += snap.refined_value
    unit_price: dict[int, Decimal] = {}
    for ore_type_id, qty in snap_qty.items():
        if qty:
            unit_price[ore_type_id] = snap_value[ore_type_id] / qty

    # Only ores the player actually mined this month become columns.
    ore_type_ids = sorted(totals_units_by_id, key=lambda oid: ore_name[oid].lower())
    ores = [ore_name[oid] for oid in ore_type_ids]

    # Characters that mined this month, ordered by name; keep label resolution
    # graceful for a character missing from EveCharacter (fall back to its id).
    mined_char_ids = {cid for (cid, _oid) in cells}
    characters = []
    for cid in sorted(
        mined_char_ids, key=lambda c: (char_name.get(c) or str(c)).lower()
    ):
        characters.append(
            {
                "label": char_name.get(cid) or str(cid),
                "cells": [cells.get((cid, oid), 0) for oid in ore_type_ids],
            }
        )

    totals_units = [totals_units_by_id[oid] for oid in ore_type_ids]
    cent = Decimal("0.01")
    totals_isk = [
        (totals_units_by_id[oid] * unit_price.get(oid, Decimal("0"))).quantize(
            cent, rounding=ROUND_HALF_UP
        )
        for oid in ore_type_ids
    ]

    return {
        "ores": ores,
        "characters": characters,
        "totals_units": totals_units,
        "totals_isk": totals_isk,
    }


def aggregate_period(period, config=None) -> int:
    """(Re)build ``MiningSnapshot`` rows for ``period`` from raw ledger entries.

    Returns the number of snapshot rows touched. Refined value is left at 0 here
    and filled by ``core.tax.price_period``.
    """
    from whatax.models import MiningLedgerEntry, MiningSnapshot, TaxConfiguration

    config = config or TaxConfiguration.objects.get_solo()

    entries = MiningLedgerEntry.objects.filter(
        recorded_date__year=period.year, recorded_date__month=period.month
    ).select_related("structure", "structure__eve_solar_system")

    # (user_id, ore_type_id, is_excluded) -> summed quantity
    totals: dict[tuple, int] = defaultdict(int)
    excluded_cache: dict[int, bool] = {}
    users: dict[int, User] = {}

    for entry in entries:
        res = resolve_player(entry.character_id)
        struct_id = entry.structure_id
        if struct_id not in excluded_cache:
            excluded_cache[struct_id] = is_structure_excluded(entry.structure, config)
        excluded = excluded_cache[struct_id]
        totals[(res.user.id, entry.ore_type_id, excluded)] += entry.quantity
        users[res.user.id] = res.user

    seen_ids = set()
    for (user_id, ore_type_id, excluded), quantity in totals.items():
        snap, _ = MiningSnapshot.objects.update_or_create(
            tax_period=period,
            user=users[user_id],
            ore_type_id=ore_type_id,
            is_excluded=excluded,
            defaults={"quantity": quantity},
        )
        seen_ids.add(snap.id)

    # Drop stale snapshots no longer backed by ledger rows (fully idempotent).
    MiningSnapshot.objects.filter(tax_period=period).exclude(id__in=seen_ids).delete()
    return len(seen_ids)
