"""Staff Payments sub-tab: combined payments + adjustments ledger."""

import datetime as dt
from decimal import Decimal

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from django.contrib.auth.models import Permission, User
from django.test import TestCase

from whatax.core.timeutils import month_bounds
from whatax.models import (
    BalanceAdjustment,
    Payment,
    TaxPeriod,
    TaxRecord,
    WalletJournalEntry,
)


def _char(character_id, corp_id=3001):
    return EveCharacter.objects.create(
        character_id=character_id,
        character_name=f"Char {character_id}",
        corporation_id=corp_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
    )


class StaffPaymentsViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staffer", password="pw")
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

        start, end = month_bounds(2026, 5)
        self.period = TaxPeriod.objects.create(
            year=2026, month=5, period_start=start, period_end=end
        )
        self.payer = User.objects.create(username="payer")
        self.record = TaxRecord.objects.create(
            tax_period=self.period, user=self.payer, tax_due=Decimal("100.00")
        )

    def test_view_lists_payment_and_adjustment(self):
        journal = WalletJournalEntry.objects.create(
            entry_id=1,
            division=1,
            ref_type="player_donation",
            amount=Decimal("40.00"),
            date=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
            first_party_id=90001,
        )
        Payment.objects.create(
            journal_entry=journal,
            tax_record=self.record,
            user=self.payer,
            amount=Decimal("40.00"),
            date=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
            match_method=Payment.MatchMethod.AUTO,
        )
        BalanceAdjustment.objects.create(
            tax_record=self.record,
            amount=Decimal("10.00"),
            reason="credit",
            created_by=self.user,
        )

        self.client.force_login(self.user)
        resp = self.client.get("/whatax/staff/payments/")
        self.assertEqual(resp.status_code, 200)

        rows = resp.context["rows"]
        self.assertEqual(len(rows), 2)
        kinds = {row["kind"] for row in rows}
        self.assertIn("Payment (auto)", kinds)
        self.assertIn("Adjustment", kinds)
        self.assertContains(resp, "Payment (auto)")
        self.assertContains(resp, "Adjustment")
