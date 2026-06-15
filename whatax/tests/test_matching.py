"""Payment reconciliation: oldest-first allocation, idempotency, unmatched."""

import datetime as dt
from decimal import Decimal

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from django.contrib.auth.models import User
from django.test import TestCase

from whatax.core import matching
from whatax.core.timeutils import month_bounds
from whatax.models import Payment, TaxConfiguration, TaxPeriod, TaxRecord, WalletJournalEntry


def _period(month):
    start, end = month_bounds(2026, month)
    return TaxPeriod.objects.create(year=2026, month=month, period_start=start, period_end=end)


def _inflow(entry_id, amount, first_party_id, division=1):
    return WalletJournalEntry.objects.create(
        entry_id=entry_id,
        division=division,
        ref_type="player_donation",
        amount=Decimal(amount),
        date=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
        first_party_id=first_party_id,
    )


class ReconcileTest(TestCase):
    def setUp(self):
        TaxConfiguration.objects.get_solo()  # division default 1
        self.user = User.objects.create(username="payer")
        char = EveCharacter.objects.create(
            character_id=90001,
            character_name="Payer Alt",
            corporation_id=2001,
            corporation_name="Corp",
            corporation_ticker="CORP",
        )
        CharacterOwnership.objects.create(character=char, owner_hash="h1", user=self.user)
        self.older = TaxRecord.objects.create(
            tax_period=_period(4), user=self.user, tax_due=Decimal("100.00")
        )
        self.newer = TaxRecord.objects.create(
            tax_period=_period(5), user=self.user, tax_due=Decimal("100.00")
        )

    def test_oldest_first_allocation(self):
        _inflow(1, "150.00", 90001)
        matching.reconcile_payments()
        self.older.refresh_from_db()
        self.newer.refresh_from_db()
        self.assertEqual(self.older.amount_paid, Decimal("100.00"))
        self.assertEqual(self.older.status, TaxRecord.Status.PAID)
        self.assertEqual(self.newer.amount_paid, Decimal("50.00"))
        self.assertEqual(self.newer.status, TaxRecord.Status.PARTIAL)

    def test_idempotent_no_double_credit(self):
        _inflow(1, "150.00", 90001)
        matching.reconcile_payments()
        matching.reconcile_payments()
        self.older.refresh_from_db()
        self.newer.refresh_from_db()
        self.assertEqual(self.older.amount_paid, Decimal("100.00"))
        self.assertEqual(self.newer.amount_paid, Decimal("50.00"))
        self.assertEqual(Payment.objects.count(), 1)

    def test_overpayment_remainder_unallocated(self):
        _inflow(1, "250.00", 90001)
        matching.reconcile_payments()
        self.older.refresh_from_db()
        self.newer.refresh_from_db()
        self.assertEqual(self.older.amount_paid, Decimal("100.00"))
        self.assertEqual(self.newer.amount_paid, Decimal("100.00"))
        payment = Payment.objects.get()
        self.assertEqual(payment.amount, Decimal("250.00"))  # full transfer recorded

    def test_unattributed_is_unmatched(self):
        _inflow(1, "100.00", 999999)  # no ownership
        matching.reconcile_payments()
        payment = Payment.objects.get()
        self.assertEqual(payment.match_method, Payment.MatchMethod.UNMATCHED)
        self.assertIsNone(payment.tax_record)

    def test_wrong_reftype_skipped(self):
        entry = _inflow(1, "100.00", 90001)
        entry.ref_type = "market_transaction"
        entry.save()
        matching.reconcile_payments()
        self.assertEqual(Payment.objects.count(), 0)
        entry.refresh_from_db()
        self.assertTrue(entry.is_processed)
