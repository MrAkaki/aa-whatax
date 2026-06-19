"""Monthly mining table: player roll-up, unregistered red rows, months list."""

import datetime as dt

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from django.contrib.auth.models import User
from django.test import TestCase
from eveuniverse.models import EveCategory, EveGroup, EveType

from whatax.core.aggregation import monthly_mining_rows
from whatax.models import MiningLedgerEntry, MiningStructure


def _ore_type(type_id, name):
    """Create an ``EveType`` plus its required group/category chain."""
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


def _corp(corporation_id=3001):
    return EveCorporationInfo.objects.create(
        corporation_id=corporation_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
        member_count=1,
    )


class MonthlyMiningRowsTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.structure = MiningStructure.objects.create(
            structure_id=900001, corporation=self.corp, name="Refinery A"
        )
        self.ore = _ore_type(46300, "Bitumens")
        self.ore2 = _ore_type(46301, "Sylvite")

    def _entry(self, character_id, ore, quantity, date, structure=None):
        return MiningLedgerEntry.objects.create(
            structure=structure or self.structure,
            character_id=character_id,
            ore_type=ore,
            quantity=quantity,
            recorded_date=date,
            recorded_corporation_id=3001,
        )

    def test_registered_chars_roll_up_under_player(self):
        user = User.objects.create(username="bigminer")
        c1 = _char(700001)
        c2 = _char(700002)
        CharacterOwnership.objects.create(character=c1, owner_hash="h1", user=user)
        CharacterOwnership.objects.create(character=c2, owner_hash="h2", user=user)
        profile = user.profile
        profile.main_character = c1
        profile.save()

        # Two characters of the same user, same structure+ore, plus another date.
        self._entry(700001, self.ore, 100, dt.date(2026, 5, 1))
        self._entry(700002, self.ore, 250, dt.date(2026, 5, 2))
        self._entry(700001, self.ore, 50, dt.date(2026, 5, 3))

        rows = monthly_mining_rows(2026, 5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["units"], 400)
        self.assertTrue(row["is_registered"])
        self.assertEqual(row["miner_label"], "Char 700001")  # main char name
        self.assertEqual(row["structure"], "Refinery A")
        self.assertEqual(row["ore"], "Bitumens")

    def test_different_ore_makes_separate_rows(self):
        user = User.objects.create(username="m2")
        c1 = _char(700010)
        CharacterOwnership.objects.create(character=c1, owner_hash="h10", user=user)
        self._entry(700010, self.ore, 10, dt.date(2026, 5, 1))
        self._entry(700010, self.ore2, 20, dt.date(2026, 5, 1))
        rows = monthly_mining_rows(2026, 5)
        self.assertEqual(len(rows), 2)
        ores = {r["ore"]: r["units"] for r in rows}
        self.assertEqual(ores, {"Bitumens": 10, "Sylvite": 20})

    def test_unregistered_char_is_flagged_red(self):
        # An EveCharacter exists for name resolution but no CharacterOwnership.
        _char(800001)
        self._entry(800001, self.ore, 999, dt.date(2026, 5, 4))
        rows = monthly_mining_rows(2026, 5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertFalse(row["is_registered"])
        self.assertEqual(row["miner_label"], "Char 800001")
        self.assertEqual(row["units"], 999)

    def test_unregistered_unknown_char_falls_back_to_id(self):
        # No EveCharacter, no ownership -> falls back to the raw id string.
        self._entry(123456789, self.ore, 5, dt.date(2026, 5, 5))
        rows = monthly_mining_rows(2026, 5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertFalse(row["is_registered"])
        self.assertEqual(row["miner_label"], "123456789")

    def test_month_filtering(self):
        user = User.objects.create(username="m3")
        c1 = _char(700020)
        CharacterOwnership.objects.create(character=c1, owner_hash="h20", user=user)
        self._entry(700020, self.ore, 1, dt.date(2026, 4, 30))
        self._entry(700020, self.ore, 7, dt.date(2026, 5, 1))
        rows = monthly_mining_rows(2026, 5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["units"], 7)


class MiningMonthsListTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.structure = MiningStructure.objects.create(
            structure_id=900002, corporation=self.corp, name="Refinery B"
        )
        self.ore = _ore_type(46300, "Bitumens")

    def _entry(self, date):
        MiningLedgerEntry.objects.create(
            structure=self.structure,
            character_id=700100,
            ore_type=self.ore,
            quantity=1,
            recorded_date=date,
            recorded_corporation_id=3001,
        )

    def test_view_months_list_newest_first(self):
        from whatax.views import _mining_months

        self._entry(dt.date(2026, 3, 5))
        self._entry(dt.date(2026, 5, 1))
        self._entry(dt.date(2026, 5, 20))  # same month -> deduped
        self._entry(dt.date(2026, 1, 15))

        months = _mining_months()
        self.assertEqual(
            months,
            [
                {"year": 2026, "month": 5},
                {"year": 2026, "month": 3},
                {"year": 2026, "month": 1},
            ],
        )


class StaffMiningViewTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.structure = MiningStructure.objects.create(
            structure_id=900003, corporation=self.corp, name="Refinery C"
        )
        self.ore = _ore_type(46300, "Bitumens")
        self.user = User.objects.create_user(username="staffer", password="pw")
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(
            content_type__app_label="whatax", codename="manage_payments"
        )
        self.user.user_permissions.add(perm)
        # AllianceAuth gates app pages behind a registered main character.
        main = _char(111222)
        CharacterOwnership.objects.create(character=main, owner_hash="mainhash", user=self.user)
        profile = self.user.profile
        profile.main_character = main
        profile.save()

    def test_view_renders_unregistered_in_red(self):
        MiningLedgerEntry.objects.create(
            structure=self.structure,
            character_id=555555,
            ore_type=self.ore,
            quantity=42,
            recorded_date=dt.date(2026, 5, 1),
            recorded_corporation_id=3001,
        )
        self.client.force_login(self.user)
        resp = self.client.get("/whatax/staff/mining/2026/5/")
        self.assertEqual(resp.status_code, 200)
        rows = resp.context["mining_rows"]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_registered"])
        self.assertContains(resp, "text-danger")
        self.assertContains(resp, "555555")

    def test_overview_excludes_unattributed_sentinel(self):
        """The staff overview records table must not list the sentinel."""
        from django.utils import timezone

        from whatax.core.aggregation import unattributed_user
        from whatax.models import TaxPeriod, TaxRecord

        period = TaxPeriod.objects.create(
            year=2026,
            month=5,
            period_start=timezone.now(),
            period_end=timezone.now(),
        )
        sentinel = unattributed_user()
        player = User.objects.create(username="payer")
        TaxRecord.objects.create(tax_period=period, user=sentinel, tax_due=18)
        TaxRecord.objects.create(tax_period=period, user=player, tax_due=5)

        self.client.force_login(self.user)
        resp = self.client.get(f"/whatax/staff/?period={period.id}")
        self.assertEqual(resp.status_code, 200)
        record_users = {r.user_id for r in resp.context["records"]}
        self.assertIn(player.id, record_users)
        self.assertNotIn(sentinel.id, record_users)
        self.assertNotContains(resp, "whatax_unattributed")
