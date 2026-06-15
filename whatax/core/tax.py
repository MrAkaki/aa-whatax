"""Rate resolution + tax calculation (TECHNICAL.md §9).

For each player with (non-excluded) mining in the period:
    total = Σ snapshot.refined_value
    rate  = CorporationTaxRate for the main char's corp, else default_tax_rate
    tax_due = (total × rate).quantize(0.01, ROUND_HALF_UP)
then upsert the ``TaxRecord``. Wrapped in a per-period transaction and
idempotent: re-running recomputes total/rate/original_tax_due but preserves
``amount_paid``, payment links, and any staff ``TaxRecordEdit`` (tax_due keeps a
correction; original_tax_due re-tracks the computed value). ``emitted_at`` /
``due_date`` are set once at first emission and never moved.
"""

import datetime as dt
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum

from whatax.core import pricing
from whatax.core.aggregation import aggregate_period, unattributed_user
from whatax.core.matching import recompute_status
from whatax.core.timeutils import eve_now

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")


def resolve_rate_for_user(user, config):
    """Return ``(rate, flat_discount, corporation_obj, has_main)`` for a player (§9).

    ``flat_discount`` is the per-corp ISK amount subtracted from this player's
    monthly charge (floored at 0); 0 when the corp has no override.
    """
    from allianceauth.eveonline.models import EveCorporationInfo
    from whatax.models import CorporationTaxRate

    main = getattr(getattr(user, "profile", None), "main_character", None)
    if main is None:
        return config.default_tax_rate, Decimal("0"), None, False

    corp_obj = EveCorporationInfo.objects.filter(corporation_id=main.corporation_id).first()
    override = CorporationTaxRate.objects.filter(
        corporation__corporation_id=main.corporation_id
    ).first()
    if override is not None:
        return override.tax_rate, override.flat_discount, corp_obj, True
    return config.default_tax_rate, Decimal("0"), corp_obj, True


def price_period(period, provider, *, config=None):
    """Fill ``refined_value`` (and reproducibility fields) on every snapshot."""
    for snap in period.snapshots.select_related("ore_type"):
        snap.refined_value = provider.refined_value(snap.ore_type, snap.quantity)
        snap.reprocessing_yield_applied = getattr(provider, "reprocessing_yield", None)
        snap.price_basis_applied = getattr(provider, "basis", "")
        snap.save(
            update_fields=["refined_value", "reprocessing_yield_applied", "price_basis_applied"]
        )


@transaction.atomic
def calculate_period(period, *, provider=None, config=None, now=None):
    """Aggregate, price, and emit ``TaxRecord``s for ``period`` (idempotent)."""
    from whatax.models import TaxConfiguration, TaxPeriod, TaxRecord

    config = config or TaxConfiguration.objects.get_solo()
    provider = provider or pricing.provider_from_config(config)
    now = now or eve_now()

    period.state = TaxPeriod.State.CALCULATING
    period.save(update_fields=["state"])

    aggregate_period(period, config)
    price_period(period, provider, config=config)

    # Σ non-excluded refined value per player. The unattributed sentinel is not a
    # payable player — its mining is billed per unregistered character in the
    # Unregistered table (§8/§15.2), so it never gets a TaxRecord of its own.
    sentinel_id = unattributed_user().id
    rows = (
        period.snapshots.filter(is_excluded=False)
        .exclude(user_id=sentinel_id)
        .values("user")
        .annotate(total=Sum("refined_value"))
    )
    for row in rows:
        user_id = row["user"]
        total = row["total"] or Decimal("0")
        record = TaxRecord.objects.select_related("user__profile").filter(
            tax_period=period, user_id=user_id
        ).first()
        if record is None:
            record = TaxRecord(tax_period=period, user_id=user_id)

        rate, discount, corp_obj, _has_main = resolve_rate_for_user(record.user, config)
        gross = (total * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        # Flat corp discount reduces the charge but never below 0; only the
        # portion actually used is recorded (so it reconciles: gross − applied).
        applied_discount = min(discount, gross) if discount > 0 else Decimal("0")
        charge = gross - applied_discount

        record.total_mined_value = total
        record.tax_rate_applied = rate
        record.flat_discount_applied = applied_discount
        record.original_tax_due = charge
        # A staff market-correction edit wins; otherwise tax_due tracks the calc.
        if not record.pk or not record.edits.exists():
            record.tax_due = charge
        record.corporation_at_calc = corp_obj
        if record.emitted_at is None:
            record.emitted_at = now
            record.due_date = now + dt.timedelta(days=config.grace_period_days)
        record.save()
        recompute_status(record)

    period.state = TaxPeriod.State.FINALIZED
    period.calculated_at = now
    period.save(update_fields=["state", "calculated_at"])
    return period


def apply_tax_edit(record, new_tax_due: Decimal, *, reason: str, user, now=None):
    """Staff correction of ``tax_due`` within the edit window (§5.4/§15.2).

    Server-side guard: only allowed while ``now <= emitted_at +
    tax_edit_window_days``. ``original_tax_due`` is never touched. Returns the
    created ``TaxRecordEdit``.
    """
    from whatax.models import TaxConfiguration, TaxRecordEdit

    config = TaxConfiguration.objects.get_solo()
    now = now or eve_now()
    if record.emitted_at is None:
        raise ValueError("cannot edit a bill that has not been emitted")
    deadline = record.emitted_at + dt.timedelta(days=config.tax_edit_window_days)
    if now > deadline:
        raise ValueError("tax edit window has closed")

    new_tax_due = Decimal(new_tax_due).quantize(CENT, rounding=ROUND_HALF_UP)
    edit = TaxRecordEdit.objects.create(
        tax_record=record,
        old_tax_due=record.tax_due,
        new_tax_due=new_tax_due,
        reason=reason,
        edited_by=user,
    )
    record.tax_due = new_tax_due
    record.save(update_fields=["tax_due"])
    recompute_status(record)
    return edit
