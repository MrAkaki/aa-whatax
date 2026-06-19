"""Moon dead/pop detection."""

import datetime as dt
from decimal import Decimal

import yaml
from eveuniverse.models import EveType

from whatax import app_settings
from whatax.core.timeutils import eve_now

# eveuniverse EveGroup ids for moon ores; the seed set for the good-ore default.
MOON_ORE_GROUP_IDS = [1884, 1920, 1921, 1922, 1923]

# Security-class banding, the single source so calc and UI agree.
HIGHSEC = "highsec"
LOWSEC = "lowsec"
NULLSEC = "nullsec"


def sec_class(security_status) -> str:
    """Band a solar-system security status: HS >= 0.5, LS 0.1-0.45, NS <= 0.0."""
    rounded = Decimal(str(security_status)).quantize(Decimal("0.1"))
    if rounded >= Decimal("0.5"):
        return HIGHSEC
    if rounded <= Decimal("0.0"):
        return NULLSEC
    return LOWSEC


def is_sec_class_excluded(system, config) -> bool:
    """True if ``system``'s security class is excluded by config; null is not excluded."""
    if system is None:
        return False
    cls = sec_class(system.security_status)
    return (
        (cls == HIGHSEC and config.exclude_highsec)
        or (cls == LOWSEC and config.exclude_lowsec)
        or (cls == NULLSEC and config.exclude_nullsec)
    )


def is_structure_excluded(structure, config) -> bool:
    """Excluded if the per-structure toggle is off OR its sec class is excluded."""
    if not structure.is_active:
        return True
    return is_sec_class_excluded(structure.eve_solar_system, config)


# EVE notification timestamps are LDAP/Windows FILETIME (100-ns ticks since 1601).
_EPOCH_DIFF = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def ldap_to_datetime(value) -> dt.datetime:
    """Convert an LDAP/FILETIME integer to a whole-second UTC ``datetime``."""
    seconds = round(int(value) / 10_000_000 - _EPOCH_DIFF)
    return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)


def parse_extraction_started(text: str) -> dict:
    """Parse a ``MoonminingExtractionStarted`` notification body (YAML)."""
    data = yaml.safe_load(text) or {}
    ore = {int(k): Decimal(str(v)) for k, v in (data.get("oreVolumeByType") or {}).items()}
    return {
        "structure_id": data.get("structureID"),
        "moon_id": data.get("moonID"),
        "chunk_arrival_time": ldap_to_datetime(data["readyTime"]) if data.get("readyTime") else None,
        "natural_decay_time": ldap_to_datetime(data["autoTime"]) if data.get("autoTime") else None,
        "ore_volume_by_type": ore,
    }


def good_ore_ids_for(structure) -> set[int]:
    """Effective good-ore type IDs: defaults minus structure excludes plus includes."""
    from whatax.models import GoodOreDefault, StructureGoodOre

    defaults = set(GoodOreDefault.objects.values_list("ore_type_id", flat=True))
    overrides = StructureGoodOre.objects.filter(structure=structure).values_list(
        "ore_type_id", "include"
    )
    includes = {tid for tid, inc in overrides if inc}
    excludes = {tid for tid, inc in overrides if not inc}
    return (defaults - excludes) | includes


def recompute_dead(extraction, threshold: Decimal | None = None) -> bool:
    """Recompute the dead-% and flip to ``dead`` past the threshold."""
    from whatax.models import MiningLedgerEntry, MoonExtraction

    threshold = threshold if threshold is not None else app_settings.WHATAX_DEAD_THRESHOLD

    good_ore_ids = good_ore_ids_for(extraction.structure)
    ores = list(extraction.ores.all())

    # Denominator: good-ore volume in the composition snapshot; refresh per-ore flags.
    total = Decimal("0")
    for ore in ores:
        is_good = ore.ore_type_id in good_ore_ids
        if ore.is_good_ore != is_good:
            ore.is_good_ore = is_good
            ore.save(update_fields=["is_good_ore"])
        if is_good:
            total += Decimal(str(ore.volume_m3 or 0))

    if total <= 0:
        # Composition unknown or no good ore; never fabricate a denominator.
        if extraction.total_good_ore_m3 is not None:
            extraction.total_good_ore_m3 = None
            extraction.save(update_fields=["total_good_ore_m3"])
        return False

    extraction.total_good_ore_m3 = total

    # Numerator: good ore mined (ledger quantity × per-unit volume) since arrival.
    volumes = {
        t.id: Decimal(str(t.volume or 0))
        for t in EveType.objects.filter(id__in=good_ore_ids)
    }
    rows = MiningLedgerEntry.objects.filter(
        structure=extraction.structure,
        ore_type_id__in=good_ore_ids,
        recorded_date__gte=extraction.chunk_arrival_time.date(),
    )
    mined = sum(
        (Decimal(r.quantity) * volumes.get(r.ore_type_id, Decimal("0")) for r in rows),
        Decimal("0"),
    )
    extraction.mined_good_ore_m3 = mined

    crossed = False
    if extraction.status != MoonExtraction.Status.DEAD and mined / total >= threshold:
        extraction.status = MoonExtraction.Status.DEAD
        extraction.dead_at = eve_now()
        crossed = True
    extraction.save(
        update_fields=["mined_good_ore_m3", "total_good_ore_m3", "status", "dead_at"]
    )
    return crossed
