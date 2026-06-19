"""player_ore_breakdown: per-character ore pivot + snapshot-priced ISK row."""

import datetime as dt
from decimal import Decimal

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from eveuniverse.models import EveCategory, EveGroup, EveType

from whatax.core.aggregation import player_ore_breakdown
from whatax.models import (
    MiningLedgerEntry,
    MiningSnapshot,
    MiningStructure,
    TaxPeriod,
)


def _ore_type(type_id, name):
    cat, _ = EveCategory.objects.get_or_create(
        id=25, defaults={"name": "Asteroid", "published": True}
    )
    grp, _ = EveGroup.objects.get_or_create(
        id=1884, defaults={"name": "Moon Materials", "eve_category": cat, "published": True}
    )
    return EveType.objects.create(id=type_id, name=name, eve_group=grp, published=True)


def _char(character_id, corp_id=3001):
    return EveCharacter.objects.create(
        character_id=character_id,
        character_name=f"Char {character_id}",
        corporation_id=corp_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
    )


class PlayerOreBreakdownTest(TestCase):
    def setUp(self):
        self.corp = EveCorporationInfo.objects.create(
            corporation_id=3001,
            corporation_name="Corp",
            corporation_ticker="CORP",
            member_count=1,
        )
        self.structure = MiningStructure.objects.create(
            structure_id=900001, corporation=self.corp, name="Refinery A"
        )
        self.bitumens = _ore_type(46300, "Bitumens")
        self.sylvite = _ore_type(46301, "Sylvite")

        self.period = TaxPeriod.objects.create(
            year=2026,
            month=5,
            period_start=timezone.now(),
            period_end=timezone.now(),
        )

        # One player, two characters.
        self.user = User.objects.create(username="bigminer")
        self.c1 = _char(700001)
        self.c2 = _char(700002)
        CharacterOwnership.objects.create(character=self.c1, owner_hash="h1", user=self.user)
        CharacterOwnership.objects.create(character=self.c2, owner_hash="h2", user=self.user)

    def _entry(self, character_id, ore, quantity, day):
        MiningLedgerEntry.objects.create(
            structure=self.structure,
            character_id=character_id,
            ore_type=ore,
            quantity=quantity,
            recorded_date=dt.date(2026, 5, day),
            recorded_corporation_id=3001,
        )

    def test_pivot_columns_rows_totals_and_isk(self):
        # c1 mines both ores, c2 only Bitumens.
        self._entry(700001, self.bitumens, 100, 1)
        self._entry(700001, self.sylvite, 40, 2)
        self._entry(700002, self.bitumens, 250, 3)

        # Snapshots set the refined unit price.
        MiningSnapshot.objects.create(
            tax_period=self.period,
            user=self.user,
            ore_type=self.bitumens,
            quantity=350,
            refined_value=Decimal("700"),
            is_excluded=False,
        )
        MiningSnapshot.objects.create(
            tax_period=self.period,
            user=self.user,
            ore_type=self.sylvite,
            quantity=40,
            refined_value=Decimal("200"),
            is_excluded=False,
        )

        out = player_ore_breakdown(self.period, self.user)

        # Columns: ores actually mined, alphabetical.
        self.assertEqual(out["ores"], ["Bitumens", "Sylvite"])

        # Rows: one per character that mined, by name.
        labels = [c["label"] for c in out["characters"]]
        self.assertEqual(labels, ["Char 700001", "Char 700002"])

        by_label = {c["label"]: c["cells"] for c in out["characters"]}
        # Cells aligned to ores [Bitumens, Sylvite].
        self.assertEqual(by_label["Char 700001"], [100, 40])
        self.assertEqual(by_label["Char 700002"], [250, 0])

        # Total units per ore across characters.
        self.assertEqual(out["totals_units"], [350, 40])

        # ISK value = total units * snapshot unit price, rounded to cents.
        self.assertEqual(out["totals_isk"], [Decimal("700.00"), Decimal("200.00")])

    def test_unpriced_ore_yields_zero_isk(self):
        # Mined but no snapshot -> unit price 0, ISK 0 (never a crash).
        self._entry(700001, self.bitumens, 10, 1)
        out = player_ore_breakdown(self.period, self.user)
        self.assertEqual(out["ores"], ["Bitumens"])
        self.assertEqual(out["totals_units"], [10])
        self.assertEqual(out["totals_isk"], [Decimal("0.00")])
