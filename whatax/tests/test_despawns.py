"""Despawn report: unmined composition ores per pop, bucketed by month."""

import datetime as dt
from decimal import Decimal

from allianceauth.eveonline.models import EveCorporationInfo
from django.test import TestCase
from eveuniverse.models import EveCategory, EveGroup, EveType

from whatax.core.despawns import despawned_ores
from whatax.models import MiningLedgerEntry, MiningStructure, MoonExtraction

_POP = dt.datetime(2026, 8, 10, 6, 0, tzinfo=dt.timezone.utc)
_NOW = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def _ore(type_id, name):
    cat, _ = EveCategory.objects.get_or_create(id=25, defaults={"name": "Asteroid", "published": True})
    grp, _ = EveGroup.objects.get_or_create(
        id=1884, defaults={"name": "Moon Materials", "eve_category": cat, "published": True}
    )
    return EveType.objects.create(id=type_id, name=name, eve_group=grp, volume=Decimal("10"), published=True)


class DespawnedOresTest(TestCase):
    def setUp(self):
        corp = EveCorporationInfo.objects.create(
            corporation_id=1, corporation_name="C", corporation_ticker="C", member_count=1
        )
        self.structure = MiningStructure.objects.create(structure_id=100, corporation=corp, name="Moon A")
        self.mined, self.left = _ore(45490, "Zeolites"), _ore(45491, "Sylvite")
        self.extraction = MoonExtraction.objects.create(
            structure=self.structure,
            extraction_start_time=_POP,
            chunk_arrival_time=_POP,
            popped_at=_POP,
            status=MoonExtraction.Status.POPPED,
        )
        for ore in (self.mined, self.left):
            self.extraction.ores.create(ore_type=ore, volume_m3=Decimal("1000"))

    def _mine(self, ore, date):
        MiningLedgerEntry.objects.create(
            structure=self.structure, character_id=1, ore_type=ore, quantity=5,
            recorded_date=date, recorded_corporation_id=1,
        )

    def test_unmined_ore_is_reported(self):
        self._mine(self.mined, _POP.date())
        rows = despawned_ores(2026, 8, _NOW)
        self.assertEqual([r["ore"] for r in rows], ["Sylvite"])
        self.assertEqual(rows[0]["moon"], "Moon A")

    def test_mining_after_belt_expiry_does_not_count(self):
        self._mine(self.left, _POP.date() + dt.timedelta(days=10))
        self.assertEqual({r["ore"] for r in despawned_ores(2026, 8, _NOW)}, {"Zeolites", "Sylvite"})

    def test_other_month_and_live_belt_excluded(self):
        self.assertEqual(despawned_ores(2026, 7, _NOW), [])
        self.assertEqual(despawned_ores(2026, 8, _POP + dt.timedelta(hours=10)), [])
