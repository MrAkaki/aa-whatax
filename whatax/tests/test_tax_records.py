"""TaxRecord status, balance adjustments, and the staff tax-edit window."""

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from freezegun import freeze_time

from whatax.core import matching, tax
from whatax.core.timeutils import month_bounds
from whatax.models import TaxConfiguration, TaxPeriod, TaxRecord, TaxRecordEdit


def _period(year=2026, month=5):
    start, end = month_bounds(year, month)
    return TaxPeriod.objects.create(year=year, month=month, period_start=start, period_end=end)


def _record(user, period, **kw):
    defaults = dict(tax_due=Decimal("100.00"), original_tax_due=Decimal("100.00"))
    defaults.update(kw)
    return TaxRecord.objects.create(tax_period=period, user=user, **defaults)


class StatusTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="miner")
        self.period = _period()

    def test_pending_then_paid(self):
        rec = _record(self.user, self.period)
        self.assertEqual(matching.recompute_status(rec), TaxRecord.Status.PENDING)
        rec.amount_paid = Decimal("100.00")
        self.assertEqual(matching.recompute_status(rec), TaxRecord.Status.PAID)

    def test_partial(self):
        rec = _record(self.user, self.period, amount_paid=Decimal("40.00"))
        self.assertEqual(matching.recompute_status(rec), TaxRecord.Status.PARTIAL)

    def test_overdue(self):
        past = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        rec = _record(self.user, self.period, due_date=past)
        self.assertEqual(matching.recompute_status(rec), TaxRecord.Status.OVERDUE)

    def test_waived_preserved(self):
        rec = _record(self.user, self.period, status=TaxRecord.Status.WAIVED)
        rec.amount_paid = Decimal("100.00")
        self.assertEqual(matching.recompute_status(rec), TaxRecord.Status.WAIVED)

    def test_adjustment_credits_balance(self):
        rec = _record(self.user, self.period)
        matching.add_balance_adjustment(rec, Decimal("100.00"), reason="credit", user=self.user)
        rec.refresh_from_db()
        self.assertEqual(rec.balance, Decimal("0.00"))
        self.assertEqual(rec.status, TaxRecord.Status.PAID)


class TaxEditWindowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="officer")
        self.period = _period()
        config = TaxConfiguration.objects.get_solo()
        config.tax_edit_window_days = 15
        config.save()
        self.emitted = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)

    def _record(self):
        return _record(self.user, self.period, emitted_at=self.emitted)

    def test_inside_window(self):
        rec = self._record()
        with freeze_time("2026-06-10"):
            tax.apply_tax_edit(rec, Decimal("50.00"), reason="price spike", user=self.user)
        rec.refresh_from_db()
        self.assertEqual(rec.tax_due, Decimal("50.00"))
        self.assertEqual(rec.original_tax_due, Decimal("100.00"))  # untouched
        self.assertEqual(TaxRecordEdit.objects.filter(tax_record=rec).count(), 1)

    def test_exactly_on_boundary_allowed(self):
        rec = self._record()
        with freeze_time("2026-06-16 00:00:00"):  # emitted + 15 days
            tax.apply_tax_edit(rec, Decimal("50.00"), reason="ok", user=self.user)
        rec.refresh_from_db()
        self.assertEqual(rec.tax_due, Decimal("50.00"))

    def test_outside_window_refused(self):
        rec = self._record()
        with freeze_time("2026-06-20"):
            with self.assertRaises(ValueError):
                tax.apply_tax_edit(rec, Decimal("50.00"), reason="too late", user=self.user)
        rec.refresh_from_db()
        self.assertEqual(rec.tax_due, Decimal("100.00"))
