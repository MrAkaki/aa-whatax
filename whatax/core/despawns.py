"""Ore despawn report: composition ores of a pop that nobody mined before the belt expired."""

import datetime as dt
from decimal import Decimal

from whatax.core import moons
from whatax.core.timeutils import eve_now


def _pct(part, whole) -> Decimal:
    return (part / whole * 100).quantize(Decimal("0.1")) if whole else Decimal("0.0")


def month_bounds(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) of a calendar month in UTC (EVE time)."""
    start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return start, end


def despawned_ores(year: int, month: int, now: dt.datetime | None = None) -> list[dict]:
    """One row per (pop, ore) that was never mined within the belt's lifetime.

    ``pct_of_chunk`` is the ore's share of the whole chunk; ``pop_despawn_pct`` is the
    share of the chunk that despawned in total for that pop (same on each of its rows).

    Pops are bucketed by pop date; belts that haven't despawned yet and
    extractions with an unknown composition are left out. Ledger rows are
    date-granular, so "mined" means any entry inside ``moons.mining_window``.
    """
    from django.db.models import Q

    from whatax.models import MiningLedgerEntry, MoonExtraction

    now = now or eve_now()
    start, end = month_bounds(year, month)
    extractions = list(
        MoonExtraction.objects.filter(
            status__in=[MoonExtraction.Status.POPPED, MoonExtraction.Status.DEAD]
        )
        .filter(
            Q(popped_at__gte=start, popped_at__lt=end)
            | Q(popped_at__isnull=True, chunk_arrival_time__gte=start, chunk_arrival_time__lt=end)
        )
        .select_related("structure", "eve_moon")
        .prefetch_related("ores__ore_type")
        .order_by("popped_at", "chunk_arrival_time")
    )
    extractions = [
        e for e in extractions if moons.despawn_time(e) <= now and list(e.ores.all())
    ]
    if not extractions:
        return []

    windows = {e.pk: moons.mining_window(e) for e in extractions}
    mined: dict[tuple[int, int], set[dt.date]] = {}
    ledger = MiningLedgerEntry.objects.filter(
        structure_id__in={e.structure_id for e in extractions},
        recorded_date__gte=min(w[0] for w in windows.values()),
        recorded_date__lte=max(w[1] for w in windows.values()),
        quantity__gt=0,
    ).values_list("structure_id", "ore_type_id", "recorded_date")
    for structure_id, ore_type_id, date in ledger:
        mined.setdefault((structure_id, ore_type_id), set()).add(date)

    rows = []
    for e in extractions:
        win_start, win_end = windows[e.pk]
        pop = e.popped_at or e.chunk_arrival_time
        name = (e.eve_moon.name if e.eve_moon else "") or e.structure.name or str(e.structure)
        ores = list(e.ores.all())
        chunk_total = sum((o.volume_m3 for o in ores), Decimal("0"))
        gone = [
            o
            for o in ores
            if not any(
                win_start <= d <= win_end for d in mined.get((e.structure_id, o.ore_type_id), ())
            )
        ]
        pop_pct = _pct(sum((o.volume_m3 for o in gone), Decimal("0")), chunk_total)
        for ore in gone:
            rows.append(
                {
                    "moon": name,
                    "structure": e.structure,
                    "pop": pop,
                    "ore": ore.ore_type.name,
                    "volume_m3": ore.volume_m3,
                    "is_good_ore": ore.is_good_ore,
                    "pct_of_chunk": _pct(ore.volume_m3, chunk_total),
                    "pop_despawn_pct": pop_pct,
                }
            )
    return rows
