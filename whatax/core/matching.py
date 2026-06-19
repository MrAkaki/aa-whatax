"""Wallet inflow to tax record matching."""

import logging
from decimal import Decimal

from django.db import transaction

from whatax.core.timeutils import eve_now

logger = logging.getLogger(__name__)

# Wallet ref_types treated as a tax payment inflow.
PAYMENT_REF_TYPES = {"player_donation"}


def recompute_status(record):
    """Recompute and persist ``status`` from the settled total."""
    from whatax.models import TaxRecord

    if record.status == TaxRecord.Status.WAIVED:
        return record.status

    settled = record.settled
    tax_due = record.tax_due
    if tax_due <= 0 or settled >= tax_due:
        status = TaxRecord.Status.PAID
    elif settled > 0:
        status = TaxRecord.Status.PARTIAL
    elif record.due_date is not None and eve_now() > record.due_date:
        status = TaxRecord.Status.OVERDUE
    else:
        status = TaxRecord.Status.PENDING

    if status != record.status:
        record.status = status
        record.save(update_fields=["status"])
    return status


def _outstanding_records(user):
    from whatax.models import TaxRecord

    return list(
        TaxRecord.objects.filter(user=user)
        .exclude(status=TaxRecord.Status.WAIVED)
        .order_by("tax_period__year", "tax_period__month")
    )


@transaction.atomic
def reconcile_payments(config=None) -> int:
    """Match unprocessed wallet inflows to outstanding bills. Returns count matched."""
    from whatax.models import Payment, TaxConfiguration, WalletJournalEntry
    from whatax.core.aggregation import resolve_player, unattributed_user

    config = config or TaxConfiguration.objects.get_solo()
    sentinel = unattributed_user()

    inflows = WalletJournalEntry.objects.filter(
        is_processed=False, amount__gt=0
    ).order_by("date", "entry_id")

    matched = 0
    for entry in inflows:
        if entry.ref_type not in PAYMENT_REF_TYPES or entry.division != config.payment_wallet_division:
            entry.is_processed = True
            entry.save(update_fields=["is_processed"])
            continue

        res = resolve_player(entry.first_party_id) if entry.first_party_id else None
        user = res.user if res else None
        is_attributed = user is not None and user.id != sentinel.id

        if not is_attributed:
            Payment.objects.create(
                journal_entry=entry,
                tax_record=None,
                character_id=entry.first_party_id,
                user=user if user and user.id != sentinel.id else None,
                amount=entry.amount,
                date=entry.date,
                match_method=Payment.MatchMethod.UNMATCHED,
            )
            entry.is_processed = True
            entry.save(update_fields=["is_processed"])
            continue

        remaining = entry.amount
        first_record = None
        for record in _outstanding_records(user):
            owed = record.tax_due - record.settled
            if owed <= 0:
                continue
            pay = min(remaining, owed)
            record.amount_paid = record.amount_paid + pay
            record.save(update_fields=["amount_paid"])
            recompute_status(record)
            if first_record is None:
                first_record = record
            remaining -= pay
            if remaining <= 0:
                break

        Payment.objects.create(
            journal_entry=entry,
            tax_record=first_record,
            character_id=entry.first_party_id,
            user=user,
            amount=entry.amount,
            date=entry.date,
            match_method=(
                Payment.MatchMethod.AUTO if first_record else Payment.MatchMethod.UNMATCHED
            ),
        )
        entry.is_processed = True
        entry.save(update_fields=["is_processed"])
        if first_record:
            matched += 1
    return matched


def add_balance_adjustment(record, amount: Decimal, *, reason: str, user):
    """Staff manual credit/debit on a record; recomputes status."""
    from whatax.models import BalanceAdjustment

    adj = BalanceAdjustment.objects.create(
        tax_record=record, amount=Decimal(amount), reason=reason, created_by=user
    )
    recompute_status(record)
    return adj


def assign_payment(payment, record, *, user=None):
    """Manually (re)assign an unmatched payment to a record (Staff action)."""
    if payment.tax_record_id and payment.tax_record_id != getattr(record, "id", None):
        old = payment.tax_record
        old.amount_paid = max(Decimal("0"), old.amount_paid - payment.amount)
        old.save(update_fields=["amount_paid"])
        recompute_status(old)

    payment.tax_record = record
    payment.match_method = payment.MatchMethod.MANUAL
    if record is not None:
        payment.user = record.user
        record.amount_paid = record.amount_paid + payment.amount
        record.save(update_fields=["amount_paid"])
        recompute_status(record)
    payment.save(update_fields=["tax_record", "match_method", "user"])
    return payment
