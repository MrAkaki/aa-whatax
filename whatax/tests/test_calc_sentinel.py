"""calculate_period never bills the unattributed sentinel (§8/§15.2).

The sentinel holds mining we can't attribute to a registered player; that ISK is
surfaced per unregistered character in the Unregistered table, so the sentinel
must not get a ``TaxRecord`` of its own (which nobody could pay and which would
double-count the per-character tax).
"""

import datetime as dt
from decimal import Decimal

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from django.contrib.auth.models import User
from django.test import TestCase
from eveuniverse.models import EveCategory, EveGroup, EveType

from whatax.core import tax
from whatax.core.aggregation import unattributed_user
from whatax.models import (
    MiningLedgerEntry,
    MiningStructure,
    TaxConfiguration,
    TaxPeriod,
    TaxRecord,
)


def _ore_type(type_id=46300, name="Bitumens"):
    cat, _ = EveCategory.objects.get_or_create(
        id=25, defaults={"name": "Asteroid", "published": True}
    )
    grp, _ = EveGroup.objects.get_or_create(
        id=1884, defaults={"name": "Moon Materials", "eve_category": cat, "published": True}
    )
    return EveType.objects.create(id=type_id, name=name, eve_group=grp, published=True)


class _StubProvider:
    """Prices 1 ISK of refined value per unit, deterministically."""

    reprocessing_yield = Decimal("0.78")
    basis = "test"

    def refined_value(self, ore_type, quantity):
        return Decimal(quantity)


class CalcSkipsSentinelTest(TestCase):
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
        self.ore = _ore_type()
        self.config = TaxConfiguration.objects.get_solo()

        # A registered player (gets a bill) ...
        self.player = User.objects.create(username="payer")
        main = EveCharacter.objects.create(
            character_id=700001,
            character_name="Main",
            corporation_id=3001,
            corporation_name="Corp",
            corporation_ticker="CORP",
        )
        CharacterOwnership.objects.create(character=main, owner_hash="h1", user=self.player)
        profile = self.player.profile
        profile.main_character = main
        profile.save()

        self.period = TaxPeriod.objects.create(
            year=2026,
            month=5,
            period_start=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
            period_end=dt.datetime(2026, 5, 31, tzinfo=dt.timezone.utc),
        )

    def _entry(self, character_id, quantity):
        return MiningLedgerEntry.objects.create(
            structure=self.structure,
            character_id=character_id,
            ore_type=self.ore,
            quantity=quantity,
            recorded_date=dt.date(2026, 5, 10),
            recorded_corporation_id=3001,
        )

    def test_sentinel_gets_no_tax_record(self):
        self._entry(700001, 1000)  # registered -> player
        self._entry(999999, 5000)  # unregistered -> sentinel

        tax.calculate_period(self.period, provider=_StubProvider(), config=self.config)

        sentinel = unattributed_user()
        self.assertFalse(
            TaxRecord.objects.filter(tax_period=self.period, user=sentinel).exists()
        )
        # The real player is still billed, and the sentinel's snapshots survive
        # (they price the Unregistered table).
        self.assertTrue(
            TaxRecord.objects.filter(tax_period=self.period, user=self.player).exists()
        )
        self.assertTrue(
            self.period.snapshots.filter(user=sentinel).exists()
        )
