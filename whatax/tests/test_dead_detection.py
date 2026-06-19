"""Dead-detection over the global-default + per-structure-override good-ore set."""

import datetime as dt
from decimal import Decimal

from allianceauth.eveonline.models import EveCorporationInfo
from django.test import TestCase
from eveuniverse.models import EveCategory, EveGroup, EveType

from whatax.core import moons
from whatax.models import (
    GoodOreDefault,
    MoonExtraction,
    MiningLedgerEntry,
    MiningStructure,
    StructureGoodOre,
)

_ARRIVAL = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)


def _ore_type(type_id, name, volume="10"):
    cat, _ = EveCategory.objects.get_or_create(
        id=25, defaults={"name": "Asteroid", "published": True}
    )
    grp, _ = EveGroup.objects.get_or_create(
        id=1884, defaults={"name": "Moon Materials", "eve_category": cat, "published": True}
    )
    return EveType.objects.create(
        id=type_id, name=name, eve_group=grp, volume=Decimal(volume), published=True
    )


class GoodOreResolverTest(TestCase):
    def setUp(self):
        self.corp = EveCorporationInfo.objects.create(
            corporation_id=1, corporation_name="C", corporation_ticker="C", member_count=1
        )
        self.structure = MiningStructure.objects.create(
            structure_id=100, corporation=self.corp, name="S"
        )
        self.a = _ore_type(45490, "A")
        self.b = _ore_type(45491, "B")
        self.c = _ore_type(45492, "C")

    def test_defaults_minus_excludes_plus_includes(self):
        GoodOreDefault.objects.create(ore_type=self.a)
        GoodOreDefault.objects.create(ore_type=self.b)
        StructureGoodOre.objects.create(structure=self.structure, ore_type=self.b, include=False)
        StructureGoodOre.objects.create(structure=self.structure, ore_type=self.c, include=True)
        self.assertEqual(
            moons.good_ore_ids_for(self.structure), {self.a.id, self.c.id}
        )


class RecomputeDeadTest(TestCase):
    def setUp(self):
        self.corp = EveCorporationInfo.objects.create(
            corporation_id=1, corporation_name="C", corporation_ticker="C", member_count=1
        )
        self.structure = MiningStructure.objects.create(
            structure_id=100, corporation=self.corp, name="S"
        )
        self.ore = _ore_type(45490, "Zeolites", volume="10")
        self.extraction = MoonExtraction.objects.create(
            structure=self.structure,
            extraction_start_time=_ARRIVAL,
            chunk_arrival_time=_ARRIVAL,
            status=MoonExtraction.Status.POPPED,
        )
        # Chunk composition: 1000 m³ = the denominator.
        self.extraction.ores.create(ore_type=self.ore, volume_m3=Decimal("1000"), is_good_ore=False)

    def _mine(self, quantity):
        MiningLedgerEntry.objects.create(
            structure=self.structure,
            character_id=42,
            ore_type=self.ore,
            quantity=quantity,
            recorded_date=_ARRIVAL.date(),
            recorded_corporation_id=1,
        )

    def test_flips_to_dead_past_threshold(self):
        GoodOreDefault.objects.create(ore_type=self.ore)
        self._mine(96)  # 96 × 10 = 960 m³ mined / 1000 = 0.96 >= 0.95
        crossed = moons.recompute_dead(self.extraction)
        self.extraction.refresh_from_db()
        self.assertTrue(crossed)
        self.assertEqual(self.extraction.status, MoonExtraction.Status.DEAD)
        self.assertIsNotNone(self.extraction.dead_at)
        self.assertEqual(self.extraction.total_good_ore_m3, Decimal("1000"))

    def test_below_threshold_stays_popped(self):
        GoodOreDefault.objects.create(ore_type=self.ore)
        self._mine(50)  # 0.50 < 0.95
        crossed = moons.recompute_dead(self.extraction)
        self.extraction.refresh_from_db()
        self.assertFalse(crossed)
        self.assertEqual(self.extraction.status, MoonExtraction.Status.POPPED)

    def test_no_good_ore_leaves_null_denominator(self):
        # No good ore -> denominator unknown -> NULL, skip.
        self.extraction.total_good_ore_m3 = Decimal("0")
        self.extraction.save(update_fields=["total_good_ore_m3"])
        self._mine(96)
        crossed = moons.recompute_dead(self.extraction)
        self.extraction.refresh_from_db()
        self.assertFalse(crossed)
        self.assertEqual(self.extraction.status, MoonExtraction.Status.POPPED)
        self.assertIsNone(self.extraction.total_good_ore_m3)

    def test_config_change_is_retroactive(self):
        # No good ore -> skip; then seed the default and re-run.
        self._mine(96)
        self.assertFalse(moons.recompute_dead(self.extraction))
        GoodOreDefault.objects.create(ore_type=self.ore)
        crossed = moons.recompute_dead(self.extraction)
        self.extraction.refresh_from_db()
        self.assertTrue(crossed)
        self.assertEqual(self.extraction.status, MoonExtraction.Status.DEAD)
        self.assertTrue(self.extraction.ores.get(ore_type=self.ore).is_good_ore)
