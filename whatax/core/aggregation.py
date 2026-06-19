"""Ledger to per-player snapshot aggregation."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from allianceauth.authentication.models import CharacterOwnership
from django.contrib.auth.models import User

from whatax.core.money import round_money
from whatax.core.moons import is_structure_excluded

logger = logging.getLogger(__name__)

UNATTRIBUTED_USERNAME = "whatax_unattributed"


def unattributed_user() -> User:
    """Sentinel player holding mining we can't attribute."""
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
    """Batch-resolve character_ids to ``(label, is_registered, user_id)``."""
    from allianceauth.eveonline.models import EveCharacter

    character_ids = set(character_ids)
    out: dict[int, tuple[str, bool, int | None]] = {}

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

    # Remaining ids are unregistered; resolve their character names.
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
    """Per (miner, structure, ore) summed-units rows for a calendar month."""
    from whatax.models import MiningLedgerEntry

    entries = MiningLedgerEntry.objects.filter(
        recorded_date__year=year, recorded_date__month=month
    ).select_related("structure", "ore_type")

    labels = _resolve_miner_labels(entries.values_list("character_id", flat=True))

    # Registered miners key on user_id so characters merge; unregistered key on char id.
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
    """Per unregistered character: refined value and tax for the period."""
    from whatax.models import MiningLedgerEntry, TaxConfiguration

    config = config or TaxConfiguration.objects.get_solo()
    sentinel = unattributed_user()

    # Per (ore_type_id, is_excluded) unit refined value, from the sentinel's snapshots.
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
    rows = []
    for character_id, label in seen.items():
        refined_value = round_money(refined[character_id])
        taxable_value = round_money(taxable[character_id])
        tax_due = round_money(taxable_value * rate)
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
    """Per-(structure, ore) mining drill-down for one unregistered character."""
    from whatax.models import MiningLedgerEntry, TaxConfiguration

    config = config or TaxConfiguration.objects.get_solo()
    sentinel = unattributed_user()

    # Per (ore_type_id, is_excluded) unit refined value, from the sentinel's snapshots.
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
    rows = []
    total_refined = Decimal("0")
    total_tax = Decimal("0")
    for key, units in grouped.items():
        info = meta[key]
        excluded = info["excluded"]
        price = unit_price.get((key[1], excluded), Decimal("0"))
        refined_value = round_money(units * price)
        tax_due = (
            Decimal("0")
            if excluded
            else round_money(units * price * rate)
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
    """Per-(character, ore) mining pivot for one player in one period."""
    return player_ore_breakdown_for_month(user, period.year, period.month)


def player_ore_breakdown_for_month(user, year: int, month: int) -> dict:
    """Per-(character, ore) mining pivot for one player in a calendar month."""
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

    # (character_id, ore_type_id) -> summed quantity.
    cells: dict[tuple[int, int], int] = defaultdict(int)
    totals_units_by_id: dict[int, int] = defaultdict(int)
    ore_name: dict[int, str] = {}
    for entry in entries:
        cells[(entry.character_id, entry.ore_type_id)] += entry.quantity
        totals_units_by_id[entry.ore_type_id] += entry.quantity
        ore_name.setdefault(entry.ore_type_id, entry.ore_type.name)

    # Per ore_type_id refined unit price from this user's snapshots; 0 if uncalculated.
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

    # Characters that mined this month, ordered by name; fall back to id if unnamed.
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
    totals_isk = [
        round_money(totals_units_by_id[oid] * unit_price.get(oid, Decimal("0")))
        for oid in ore_type_ids
    ]

    return {
        "ores": ores,
        "characters": characters,
        "totals_units": totals_units,
        "totals_isk": totals_isk,
    }


def aggregate_period(period, config=None) -> int:
    """(Re)build ``MiningSnapshot`` rows for ``period`` from raw ledger entries."""
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
