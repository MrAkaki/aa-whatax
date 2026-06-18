"""Views (TECHNICAL.md §15).

Thin entry points: resolve permissions, delegate to ``core/`` and ``managers``,
render. Object-level scoping lives in managers (``TaxRecord.objects.visible_to``),
never inline here or in templates. The UI is a tabbed single app gated by the
three §14 roles; templates hide tabs the user can't access.
"""

import datetime as dt
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Min, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from esi.decorators import token_required

# Scope bundles for the two corp tokens added from the Admin tab (TECHNICAL.md §6.1).
# One char per corp grants each bundle; the role behind the token (Director /
# Accountant) is what ESI actually checks, so a token can have the scope yet 403.
STRUCTURE_TOKEN_SCOPES = [
    "esi-corporations.read_structures.v1",  # corp structures (sync_structures)
    "esi-industry.read_corporation_mining.v1",  # observer ledger + moon extractions
    "esi-characters.read_notifications.v1",  # corp moon pop/started notifications
]
WALLET_TOKEN_SCOPES = ["esi-wallet.read_corporation_wallets.v1"]

from whatax.core import matching, tax
from whatax.core.aggregation import (
    player_ore_breakdown,
    player_ore_breakdown_for_month,
    unattributed_user,
    unregistered_character_breakdown,
    unregistered_character_rows,
)
from whatax.core.timeutils import eve_now
from whatax.forms import (
    BalanceAdjustmentForm,
    CorporationTaxRateForm,
    GoodOreDefaultForm,
    MoonGroupForm,
    OffWalletPaymentForm,
    StructureGoodOreForm,
    TaxConfigurationForm,
    TaxEditForm,
    WaiveForm,
)
from whatax.models import (
    BalanceAdjustment,
    CorporationTaxRate,
    GoodOreDefault,
    MiningLedgerEntry,
    MiningStructure,
    MoonExtraction,
    MoonGroup,
    Payment,
    RegisteredToken,
    StructureGoodOre,
    TaxConfiguration,
    TaxPeriod,
    TaxRecord,
)


# --- Dashboard (user) -------------------------------------------------------


@login_required
@permission_required("whatax.basic_access")
def index(request):
    """Dashboard: moon pops, own 6-month mining graph, own char×ore, tax (§15.1).

    Pops are bucketed per :class:`MoonGroup` (one card each, responsive 2-col grid
    in the template); a card lists its *recently popped* (not dead) and *upcoming*
    extractions by structure name and date. The mining graph charts the player's
    own refined ISK value over the latest six periods, summed from their
    ``MiningSnapshot`` rows (matches their bills; the open month reads 0 until
    calc runs). The char×ore table pivots the player's own mining for a selected
    month, with prev/next month navigation.
    """
    now = eve_now()

    upcoming_frags = MoonExtraction.objects.filter(
        chunk_arrival_time__gte=now, chunk_arrival_time__lte=now + dt.timedelta(days=2)
    ).select_related("structure", "structure__group").order_by("chunk_arrival_time")
    current_frags = MoonExtraction.objects.filter(
        status=MoonExtraction.Status.POPPED, popped_at__gte=now - dt.timedelta(days=2)
    ).select_related("structure", "structure__group").order_by("popped_at")

    # --- My mining: refined ISK value over the latest six periods (snapshots) ---
    chart_periods = list(TaxPeriod.objects.all()[:6])[::-1]  # newest-first -> chronological
    mining_bars = []
    for chart_period in chart_periods:
        total = (
            chart_period.snapshots.filter(user=request.user).aggregate(
                total=Sum("refined_value")
            )["total"]
            or Decimal("0")
        )
        mining_bars.append({"label": str(chart_period), "value": total})
    max_value = max((b["value"] for b in mining_bars), default=Decimal("0"))
    for bar in mining_bars:
        bar["pct"] = float(bar["value"] / max_value * 100) if max_value else 0

    # --- My mining: character×ore for the selected month (default current) ---
    sel_year, sel_month = _selected_month(request, now)
    prev_year, prev_month = (sel_year - 1, 12) if sel_month == 1 else (sel_year, sel_month - 1)
    next_year, next_month = (sel_year + 1, 1) if sel_month == 12 else (sel_year, sel_month + 1)

    context = {
        "active_tab": "dashboard",
        "frag_groups": _merge_frag_groups(current_frags, upcoming_frags),
        "mining_bars": mining_bars,
        "ore_breakdown": player_ore_breakdown_for_month(request.user, sel_year, sel_month),
        "sel_year": sel_year,
        "sel_month": sel_month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        # No paging into the future: hide "next" once the selection reaches now.
        "has_next": (sel_year, sel_month) < (now.year, now.month),
        "records": TaxRecord.objects.visible_to(request.user)
        .filter(user=request.user)
        .select_related("tax_period"),
        "config": TaxConfiguration.objects.get_solo(),
    }
    return render(request, "whatax/dashboard.html", context)


def _selected_month(request, now):
    """Resolve the (year, month) for the dashboard ore table from GET, default now.

    Bad / out-of-range input falls back to the current EVE month rather than 500.
    """
    try:
        year = int(request.GET.get("year", now.year))
        month = int(request.GET.get("month", now.month))
    except (TypeError, ValueError):
        return now.year, now.month
    if not 1 <= month <= 12:
        return now.year, now.month
    return year, month


def _merge_frag_groups(current_frags, upcoming_frags):
    """Bucket current (recently popped) + upcoming extractions per :class:`MoonGroup`.

    Returns ``[{"group": MoonGroup|None, "current": [...], "upcoming": [...]}]``
    ordered by group name with the ungrouped bucket last, so the template renders
    one pop card per group (§15.1). Only buckets with any pops appear.
    """
    buckets: dict = {}
    for frag in current_frags:
        buckets.setdefault(frag.structure.group, {"current": [], "upcoming": []})[
            "current"
        ].append(frag)
    for frag in upcoming_frags:
        buckets.setdefault(frag.structure.group, {"current": [], "upcoming": []})[
            "upcoming"
        ].append(frag)
    grouped = [
        {"group": group, **lists}
        for group, lists in sorted(
            (kv for kv in buckets.items() if kv[0] is not None),
            key=lambda kv: kv[0].name,
        )
    ]
    if None in buckets:
        grouped.append({"group": None, **buckets[None]})
    return grouped


# --- Staff ------------------------------------------------------------------


def _attach_character_search(records):
    """Tag each record with its player's other character names for table search.

    The staff/period record tables show one row per player (the main character's
    name). To let a search box surface a player when one of their *alts* matches
    the typed text, we stash that player's full character-name list on the row
    via ``record.character_search`` (rendered into ``data-search``; search.js
    folds it into the match text). Returns the records as a list so the template
    iterates the same objects we annotated.
    """
    from allianceauth.authentication.models import CharacterOwnership

    records = list(records)
    names: dict[int, list[str]] = {}
    for user_id, name in CharacterOwnership.objects.filter(
        user_id__in={r.user_id for r in records}
    ).values_list("user_id", "character__character_name"):
        if name:
            names.setdefault(user_id, []).append(name)
    for record in records:
        record.character_search = " ".join(names.get(record.user_id, []))
    return records


@login_required
@permission_required("whatax.manage_payments")
def staff(request):
    """All records for a period, unmatched payments, totals, ore-value graph (§15.2).

    The unattributed sentinel is excluded — its miners are surfaced per-character
    in the Unregistered table (Outstanding sub-tab), matching ``period_detail``
    and ``staff_outstanding``.

    The page also charts "total estimated ore value" per period at the top: the
    sum of every ``MiningSnapshot.refined_value`` in a period (excluded and
    non-excluded alike — the priced value of ore mined, not the tax). We chart
    the latest six periods, oldest-first for left-to-right reading, and scale
    each bar against the max so the graph stays dependency-free (pure CSS bars,
    no JS chart library, §15).
    """
    period_id = request.GET.get("period")
    period = (
        TaxPeriod.objects.filter(pk=period_id).first()
        if period_id
        else TaxPeriod.objects.first()
    )
    records = (
        TaxRecord.objects.filter(tax_period=period)
        .exclude(user=unattributed_user())
        .select_related("user", "tax_period")
        if period
        else TaxRecord.objects.none()
    )
    chart_periods = list(TaxPeriod.objects.all()[:6])[::-1]  # newest-first -> chronological
    bars = []
    for chart_period in chart_periods:
        total = chart_period.snapshots.aggregate(total=Sum("refined_value"))["total"] or Decimal("0")
        bars.append({"label": str(chart_period), "value": total})
    max_value = max((b["value"] for b in bars), default=Decimal("0"))
    for bar in bars:
        bar["pct"] = float(bar["value"] / max_value * 100) if max_value else 0
    context = {
        "active_tab": "staff",
        "active_subtab": "overview",
        "bars": bars,
        "periods": TaxPeriod.objects.all(),
        "mining_months": _mining_months(),
        "structure_groups": _group_by_moongroup(
            _structures_next_pop(), lambda s: s.group
        ),
        "period": period,
        "records": _attach_character_search(records),
        "unmatched_payments": Payment.objects.filter(
            match_method=Payment.MatchMethod.UNMATCHED
        ).select_related("user"),
        "payment_form": OffWalletPaymentForm(),
    }
    return render(request, "whatax/staff.html", context)


@login_required
@permission_required("whatax.manage_payments")
def staff_outstanding(request):
    """Staff sub-tab: who owes money and who mined unregistered (§15.2).

    Two tables, each with a grand total:
    - **Debts**: per player, the sum of negative ``TaxRecord.balance`` across all
      periods (``balance`` is a Python property, so we filter in Python, not SQL).
      The unattributed sentinel is excluded — its miners are the second table.
    - **Unregistered**: characters that mined in the current and previous month
      but have no ``CharacterOwnership``. Values reuse ``unregistered_character_rows``
      for whichever of the two periods exist, merged per character so the figures
      match the rest of the app.
    """
    sentinel = unattributed_user()

    # --- Table A: outstanding debts per player ---
    owed_by_user: dict[int, dict] = {}
    candidates = (
        TaxRecord.objects.exclude(status=TaxRecord.Status.WAIVED)
        .exclude(user=sentinel)
        .select_related("user")
    )
    for record in candidates:
        if record.balance < 0:
            entry = owed_by_user.setdefault(
                record.user_id, {"user": record.user, "owed": Decimal("0")}
            )
            entry["owed"] += -record.balance
    debt_rows = sorted(owed_by_user.values(), key=lambda r: r["owed"], reverse=True)
    debt_total = sum((r["owed"] for r in debt_rows), Decimal("0"))

    # --- Table B: unregistered miners, current + previous month ---
    now = eve_now()
    y1, m1 = now.year, now.month
    y2, m2 = (y1 - 1, 12) if m1 == 1 else (y1, m1 - 1)

    merged: dict[int, dict] = {}
    for year, month in ((y1, m1), (y2, m2)):
        period = TaxPeriod.objects.filter(year=year, month=month).first()
        if period is None:
            continue
        for row in unregistered_character_rows(period):
            entry = merged.setdefault(
                row["character_id"],
                {
                    "label": row["label"],
                    "refined_value": Decimal("0"),
                    "taxable_value": Decimal("0"),
                    "tax_due": Decimal("0"),
                },
            )
            entry["refined_value"] += row["refined_value"]
            entry["taxable_value"] += row["taxable_value"]
            entry["tax_due"] += row["tax_due"]
    unregistered_rows = sorted(merged.values(), key=lambda r: r["label"].lower())
    unregistered_total = {
        "refined_value": sum((r["refined_value"] for r in unregistered_rows), Decimal("0")),
        "taxable_value": sum((r["taxable_value"] for r in unregistered_rows), Decimal("0")),
        "tax_due": sum((r["tax_due"] for r in unregistered_rows), Decimal("0")),
    }

    context = {
        "active_tab": "staff",
        "active_subtab": "outstanding",
        "debt_rows": debt_rows,
        "debt_total": debt_total,
        "unregistered_rows": unregistered_rows,
        "unregistered_total": unregistered_total,
        "unregistered_months": [f"{y1}-{m1:02d}", f"{y2}-{m2:02d}"],
    }
    return render(request, "whatax/staff_outstanding.html", context)


@login_required
@permission_required("whatax.manage_payments")
def staff_payments(request):
    """Staff sub-tab: every payment and balance adjustment in one table (§15.2).

    A single combined, sortable ledger of money movement. Each row is a payment
    (auto/manual/unmatched) or a manual balance adjustment, attributed to the
    player it belongs to. ``Remaining`` is the *current* balance on the
    associated ``TaxRecord`` (a Python property, read here rather than in SQL);
    unmatched payments have no record, so it shows "—". The combined list is
    sorted by date descending in Python (``balance`` / the merge can't be done
    in SQL); the template's ``whatax-paginate`` table makes the headers
    click-to-sort.
    """
    payments = Payment.objects.select_related("user", "tax_record", "tax_record__user")
    adjustments = BalanceAdjustment.objects.select_related("tax_record", "tax_record__user")

    method_labels = {
        Payment.MatchMethod.AUTO: "Payment (auto)",
        Payment.MatchMethod.MANUAL: "Payment (manual)",
        Payment.MatchMethod.UNMATCHED: "Payment (unmatched)",
    }

    rows = []
    for payment in payments:
        if payment.user_id:
            character_label = str(payment.user)
        elif payment.character_id:
            character_label = str(payment.character_id)
        else:
            character_label = "—"
        rows.append(
            {
                "character_label": character_label,
                "date": payment.date,
                "amount": payment.amount,
                "remaining": payment.tax_record.balance if payment.tax_record else None,
                "kind": method_labels.get(payment.match_method, "Payment"),
            }
        )
    for adjustment in adjustments:
        record = adjustment.tax_record
        rows.append(
            {
                "character_label": str(record.user) if record else "—",
                "date": adjustment.created_at,
                "amount": adjustment.amount,
                "remaining": record.balance if record else None,
                "kind": "Adjustment",
            }
        )

    rows.sort(key=lambda r: r["date"], reverse=True)

    context = {
        "active_tab": "staff",
        "active_subtab": "payments",
        "rows": rows,
    }
    return render(request, "whatax/staff_payments.html", context)


def _group_by_moongroup(items, group_of):
    """Bucket ``items`` into per-:class:`MoonGroup` tables, ungrouped last.

    ``group_of(item)`` returns the item's :class:`MoonGroup` (or ``None``).
    Returns a list of ``{"group": MoonGroup|None, "items": [...]}`` ordered by
    group name, with the ungrouped bucket (``group`` is ``None``) appended last
    when it has any items. Order within each bucket follows ``items``. This is
    the shared shape every "structures/pops list" renders as one table per
    group plus a final table for moons that belong to no group (§5.1).
    """
    buckets: dict = {}
    for item in items:
        buckets.setdefault(group_of(item), []).append(item)
    grouped = [
        {"group": group, "items": grouped_items}
        for group, grouped_items in sorted(
            (kv for kv in buckets.items() if kv[0] is not None),
            key=lambda kv: kv[0].name,
        )
    ]
    if None in buckets:
        grouped.append({"group": None, "items": buckets[None]})
    return grouped


def _structures_next_pop():
    """All mining structures annotated with their next upcoming pop (§15.2).

    ``next_pop`` is the soonest future ``chunk_arrival_time`` (the moment a chunk
    arrives and the moon can be fractured) among extractions that haven't already
    popped, decayed or been cancelled; ``None`` when nothing is scheduled.
    """
    from whatax.models import MiningStructure

    now = eve_now()
    closed = [
        MoonExtraction.Status.POPPED,
        MoonExtraction.Status.DEAD,
        MoonExtraction.Status.CANCELLED,
    ]
    return (
        MiningStructure.objects.select_related(
            "corporation", "eve_solar_system", "group"
        )
        .annotate(
            next_pop=Min(
                "extractions__chunk_arrival_time",
                filter=Q(extractions__chunk_arrival_time__gte=now)
                & ~Q(extractions__status__in=closed),
            )
        )
        .order_by("name", "structure_id")
    )


@login_required
@permission_required("whatax.manage_payments")
def staff_mining_month(request, year, month):
    """Per-month mining table grouped by miner / structure / ore (§15.2).

    Registered characters roll up under their player; unregistered character_ids
    each get a red-flagged row. Reachable from the months list on the Staff tab.
    """
    from whatax.core.aggregation import monthly_mining_rows

    context = {
        "active_tab": "staff",
        "active_subtab": "overview",
        "mining_months": _mining_months(),
        "selected_year": year,
        "selected_month": month,
        "mining_rows": monthly_mining_rows(year, month),
    }
    return render(request, "whatax/staff_mining.html", context)


def _mining_months():
    """Distinct (year, month) present in the ledger, newest first."""
    return [
        {"year": d.year, "month": d.month}
        for d in MiningLedgerEntry.objects.dates("recorded_date", "month", order="DESC")
    ]


@login_required
@permission_required("whatax.manage_payments")
def period_detail(request, period_id):
    """Per-player / per-ore breakdown for a period, incl. excluded bucket (§15.2)."""
    period = get_object_or_404(TaxPeriod, pk=period_id)
    sentinel = unattributed_user()
    context = {
        "active_tab": "staff",
        "active_subtab": "overview",
        "period": period,
        "records": _attach_character_search(
            period.tax_records.select_related("user").exclude(user=sentinel)
        ),
        "unregistered": unregistered_character_rows(period),
        "adjust_form": BalanceAdjustmentForm(),
        "edit_form": TaxEditForm(),
        "waive_form": WaiveForm(),
    }
    return render(request, "whatax/period_detail.html", context)


@login_required
@permission_required("whatax.manage_payments")
def period_player_detail(request, period_id, user_id):
    """Per-player drill-down for a period: ore pivot + that record's tax (§15.2).

    The pivot (``player_ore_breakdown``) shows exactly what each of the player's
    characters mined this period; below it we surface the player's ``TaxRecord``
    figures (mined value, rate, flat discount, tax due) for context.
    """
    from django.contrib.auth.models import User

    period = get_object_or_404(TaxPeriod, pk=period_id)
    player = get_object_or_404(User, pk=user_id)
    record = (
        period.tax_records.select_related("user").filter(user=player).first()
    )
    context = {
        "active_tab": "staff",
        "active_subtab": "overview",
        "period": period,
        "player": player,
        "breakdown": player_ore_breakdown(period, player),
        "record": record,
    }
    return render(request, "whatax/period_player_detail.html", context)


@login_required
@permission_required("whatax.manage_payments")
def period_unregistered_detail(request, period_id, character_id):
    """Per-ore mining drill-down for one unregistered character in a period (§15.2).

    The unregistered counterpart to ``period_player_detail``: it surfaces exactly
    what an untracked miner mined this period (per structure & ore, with refined
    value and tax) so staff can see why they appear in the unregistered table.
    """
    period = get_object_or_404(TaxPeriod, pk=period_id)
    context = {
        "active_tab": "staff",
        "active_subtab": "overview",
        "period": period,
        "character_id": character_id,
        "breakdown": unregistered_character_breakdown(period, character_id),
    }
    return render(request, "whatax/period_unregistered_detail.html", context)


@login_required
@permission_required("whatax.manage_payments")
@require_POST
def payment_match(request, payment_id):
    """Assign an unmatched payment to a record (manual match)."""
    payment = get_object_or_404(Payment, pk=payment_id)
    record_id = request.POST.get("record")
    record = TaxRecord.objects.filter(pk=record_id).first() if record_id else None
    matching.assign_payment(payment, record, user=request.user)
    messages.success(request, "Payment reassigned.")
    return redirect("whatax:staff")


@login_required
@permission_required("whatax.manage_payments")
@require_POST
def record_adjust(request, record_id):
    """Add/remove balance on a record (creates an audited BalanceAdjustment)."""
    record = get_object_or_404(TaxRecord, pk=record_id)
    form = BalanceAdjustmentForm(request.POST)
    if form.is_valid():
        matching.add_balance_adjustment(
            record,
            form.cleaned_data["amount"],
            reason=form.cleaned_data["reason"],
            user=request.user,
        )
        messages.success(request, "Balance adjusted.")
    else:
        messages.error(request, "Invalid adjustment.")
    return redirect("whatax:period", period_id=record.tax_period_id)


@login_required
@permission_required("whatax.manage_payments")
@require_POST
def record_add_payment(request, record_id):
    """Record a payment a player made outside the corp wallet (Staff, §10).

    Players normally pay into the monitored wallet, but a staff member may need
    to credit a payment made another way. It is booked as an audited credit on
    the record, with the staff comment kept as the reason.
    """
    record = get_object_or_404(TaxRecord, pk=record_id)
    form = OffWalletPaymentForm(request.POST)
    if form.is_valid():
        matching.add_balance_adjustment(
            record,
            form.cleaned_data["amount"],
            reason="Off-wallet payment: " + form.cleaned_data["comment"],
            user=request.user,
        )
        messages.success(request, f"Off-wallet payment recorded for {record.user}.")
    else:
        messages.error(request, "Invalid payment (need a positive amount and a comment).")
    return redirect(reverse("whatax:staff") + f"?period={record.tax_period_id}")


@login_required
@permission_required("whatax.manage_payments")
@require_POST
def record_edit_tax(request, record_id):
    """Correct a bill's tax_due within the edit window (§15.2)."""
    record = get_object_or_404(TaxRecord, pk=record_id)
    form = TaxEditForm(request.POST)
    if form.is_valid():
        try:
            tax.apply_tax_edit(
                record,
                form.cleaned_data["new_tax_due"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
            messages.success(request, "Tax amount corrected.")
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Invalid tax edit.")
    return redirect("whatax:period", period_id=record.tax_period_id)


# --- Admin ------------------------------------------------------------------


@login_required
@permission_required("whatax.admin_access")
@require_POST
def record_waive(request, record_id):
    """Forgive a bill entirely (admin)."""
    record = get_object_or_404(TaxRecord, pk=record_id)
    form = WaiveForm(request.POST)
    if form.is_valid():
        matching.add_balance_adjustment(
            record, Decimal("0"), reason="WAIVED: " + form.cleaned_data["reason"], user=request.user
        )
        record.status = TaxRecord.Status.WAIVED
        record.save(update_fields=["status"])
        messages.success(request, "Record waived.")
    return redirect("whatax:period", period_id=record.tax_period_id)


@login_required
@permission_required("whatax.admin_access")
def admin_config(request):
    """The dangerous configuration surface (§15.3)."""
    config = TaxConfiguration.objects.get_solo()
    form = TaxConfigurationForm(instance=config)
    corp_rate_form = CorporationTaxRateForm()

    if request.method == "POST":
        which = request.POST.get("form")
        if which == "config":
            form = TaxConfigurationForm(request.POST, instance=config)
            if form.is_valid():
                form.save()
                messages.success(request, "Configuration saved.")
                return redirect("whatax:admin")
        elif which == "corp_rate":
            # OneToOne corp: bind to any existing override so adding for a corp
            # that already has one edits it instead of failing the unique check.
            existing = CorporationTaxRate.objects.filter(
                corporation_id=request.POST.get("corporation")
            ).first()
            corp_rate_form = CorporationTaxRateForm(request.POST, instance=existing)
            if corp_rate_form.is_valid():
                corp_rate_form.save()
                messages.success(request, "Corporation tax override saved.")
                return redirect("whatax:admin")

    from esi.models import Token

    context = {
        "active_tab": "admin",
        "form": form,
        "config": config,
        "corp_rates": CorporationTaxRate.objects.select_related("corporation"),
        "corp_rate_form": corp_rate_form,
        "periods": TaxPeriod.objects.all(),
        "janice_key_set": bool(config.janice_api_key),
        "structure_tokens": _annotate_corporations(
            Token.objects.filter(whatax_registration__isnull=False)
            .require_scopes(STRUCTURE_TOKEN_SCOPES)
            .require_valid()
        ),
        "wallet_tokens": _annotate_corporations(
            Token.objects.filter(whatax_registration__isnull=False)
            .require_scopes(WALLET_TOKEN_SCOPES)
            .require_valid()
        ),
    }
    return render(request, "whatax/admin.html", context)


def _annotate_corporations(tokens):
    """Attach ``corporation_name`` to each token by resolving its character.

    A token only knows the character; the char name alone is easy to confuse, so
    we also show which corp the grant covers (resolved via ``EveCharacter``).
    """
    from allianceauth.eveonline.models import EveCharacter

    tokens = list(tokens)
    corp_by_char = dict(
        EveCharacter.objects.filter(
            character_id__in=[t.character_id for t in tokens]
        ).values_list("character_id", "corporation_name")
    )
    for token in tokens:
        token.corporation_name = corp_by_char.get(token.character_id)
    return tokens


def _corp_for_character(character_id):
    """Resolve (creating from ESI if needed) the ``EveCorporationInfo`` for a char."""
    from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo

    char = EveCharacter.objects.filter(character_id=character_id).first()
    if char is None:
        return None
    corp = EveCorporationInfo.objects.filter(corporation_id=char.corporation_id).first()
    if corp is None:
        corp = EveCorporationInfo.objects.create_corporation(char.corporation_id)
    return corp


@login_required
@permission_required("whatax.admin_access")
@token_required(scopes=STRUCTURE_TOKEN_SCOPES)
def add_structures_token(request, token):
    """Add a corp token for structures, moon extractions and notifications (§6.1).

    ``@token_required`` runs the EVE SSO flow and stores the token; the synced
    tasks pick it up via ``_corp_token``. Grant from a Director-role char.
    """
    RegisteredToken.objects.update_or_create(
        token=token, defaults={"purpose": RegisteredToken.Purpose.STRUCTURES}
    )
    messages.success(
        request, f"Structures / moons token added for {token.character_name}."
    )
    return redirect("whatax:admin")


@login_required
@permission_required("whatax.admin_access")
@token_required(scopes=WALLET_TOKEN_SCOPES)
def add_wallet_token(request, token):
    """Add the payment-corp wallet token (§6.1). Grant from an Accountant-role char.

    The payment corporation is *defined by* this token: we set it to the token
    char's corp rather than have an admin pick it separately.
    """
    RegisteredToken.objects.update_or_create(
        token=token, defaults={"purpose": RegisteredToken.Purpose.WALLET}
    )
    config = TaxConfiguration.objects.get_solo()
    corp = _corp_for_character(token.character_id)
    if corp is not None:
        config.payment_corporation = corp
        config.save(update_fields=["payment_corporation"])
    messages.success(
        request,
        f"Wallet token added for {token.character_name} — payment corp set to {corp}.",
    )
    return redirect("whatax:admin")


@login_required
@permission_required("whatax.admin_access")
@require_POST
def remove_token(request, token_id):
    """Delete a corp ESI token (admin). Syncs stop using it on the next run."""
    from esi.models import Token

    token = get_object_or_404(Token, pk=token_id)
    name = token.character_name
    token.delete()
    # Payment corp is defined by the wallet token; clear it once the last valid
    # wallet token is gone so syncs don't keep targeting an unauthorised corp.
    if (
        not Token.objects.filter(whatax_registration__isnull=False)
        .require_scopes(WALLET_TOKEN_SCOPES)
        .require_valid()
        .exists()
    ):
        config = TaxConfiguration.objects.get_solo()
        if config.payment_corporation_id:
            config.payment_corporation = None
            config.save(update_fields=["payment_corporation"])
    messages.success(request, f"Token for {name} removed.")
    return redirect("whatax:admin")


@login_required
@permission_required("whatax.admin_access")
@require_POST
def run_calc(request):
    """Run / re-run calc for a period (admin)."""
    from whatax import tasks

    year = int(request.POST["year"])
    month = int(request.POST["month"])
    # Explicit admin re-run: recompute even if the period is already finalized
    # (the scheduled beat skips finalized periods; this is the repair path).
    tasks.run_monthly_tax.delay(year, month, force=True)
    messages.success(request, f"Calculation queued for {year}-{month:02d}.")
    return redirect("whatax:admin")


@login_required
@permission_required("whatax.admin_access")
@require_POST
def corp_rate_delete(request, rate_id):
    """Remove a per-corporation tax override (admin)."""
    rate = get_object_or_404(CorporationTaxRate, pk=rate_id)
    rate.delete()
    messages.success(request, "Corporation tax override removed.")
    return redirect("whatax:admin")


@login_required
@permission_required("whatax.admin_access")
def admin_good_ores(request):
    """Manage the good-ore set for moon dead-detection (§11).

    The global default list (seeded with all moon ores) applies everywhere; the
    per-structure overrides add or exclude ores at a single structure. Edits take
    effect on the next ``update_moon_status`` recompute.
    """
    default_form = GoodOreDefaultForm()
    override_form = StructureGoodOreForm()

    if request.method == "POST":
        which = request.POST.get("form")
        if which == "default":
            # Adding an ore already in the default set is a no-op, not an error.
            default_form = GoodOreDefaultForm(request.POST)
            if default_form.is_valid():
                GoodOreDefault.objects.get_or_create(
                    ore_type=default_form.cleaned_data["ore_type"]
                )
                messages.success(request, "Good-ore default added.")
                return redirect("whatax:admin_good_ores")
        elif which == "override":
            ore_type = request.POST.get("ore_type")
            structure = request.POST.get("structure")
            existing = StructureGoodOre.objects.filter(
                structure_id=structure, ore_type_id=ore_type
            ).first()
            override_form = StructureGoodOreForm(request.POST, instance=existing)
            if override_form.is_valid():
                override_form.save()
                messages.success(request, "Structure good-ore override saved.")
                return redirect("whatax:admin_good_ores")

    context = {
        "active_tab": "admin",
        "default_form": default_form,
        "override_form": override_form,
        "defaults": GoodOreDefault.objects.select_related("ore_type").order_by("ore_type__name"),
        "overrides": StructureGoodOre.objects.select_related(
            "ore_type", "structure"
        ).order_by("structure__name", "ore_type__name"),
    }
    return render(request, "whatax/admin_good_ores.html", context)


@login_required
@permission_required("whatax.admin_access")
@require_POST
def good_ore_default_delete(request, default_id):
    """Remove an ore from the global good-ore default set (admin)."""
    get_object_or_404(GoodOreDefault, pk=default_id).delete()
    messages.success(request, "Good-ore default removed.")
    return redirect("whatax:admin_good_ores")


@login_required
@permission_required("whatax.admin_access")
@require_POST
def structure_good_ore_delete(request, override_id):
    """Remove a per-structure good-ore override (admin)."""
    get_object_or_404(StructureGoodOre, pk=override_id).delete()
    messages.success(request, "Structure good-ore override removed.")
    return redirect("whatax:admin_good_ores")


@login_required
@permission_required("whatax.admin_access")
def admin_groups(request):
    """Manage moon groups: create groups and assign structures to them (§5.1).

    A structure belongs to at most one group; assigning it to a group moves it
    out of any previous one. ``schedule_interval_days`` is stored for the later
    pop-time projection. New / unassigned structures have ``group = None``.
    """
    group_form = MoonGroupForm()

    if request.method == "POST":
        which = request.POST.get("form")
        if which == "group":
            group_form = MoonGroupForm(request.POST)
            if group_form.is_valid():
                group_form.save()
                messages.success(request, "Moon group created.")
                return redirect("whatax:admin_groups")
        elif which == "assign":
            structure = get_object_or_404(MiningStructure, pk=request.POST.get("structure"))
            # Blank group id unassigns; any other value moves it to that group.
            structure.group_id = request.POST.get("group") or None
            structure.save(update_fields=["group"])
            # The cadence (or lack of one) just changed; reproject the planned pop.
            structure.recompute_planned_pop()
            messages.success(request, f"{structure} assigned.")
            return redirect("whatax:admin_groups")

    context = {
        "active_tab": "admin",
        "group_form": group_form,
        "groups": MoonGroup.objects.prefetch_related("structures").order_by("name"),
        "unassigned": MiningStructure.objects.filter(group__isnull=True).order_by("name"),
    }
    return render(request, "whatax/admin_groups.html", context)


@login_required
@permission_required("whatax.admin_access")
@require_POST
def moon_group_delete(request, group_id):
    """Delete a moon group (admin). Its structures survive, just ungrouped."""
    get_object_or_404(MoonGroup, pk=group_id).delete()
    messages.success(request, "Moon group removed.")
    return redirect("whatax:admin_groups")


@login_required
@permission_required("whatax.admin_access")
@require_POST
def moon_group_remove_structure(request, structure_id):
    """Remove a structure from its group (admin) — sets group to none."""
    structure = get_object_or_404(MiningStructure, pk=structure_id)
    structure.group = None
    structure.save(update_fields=["group"])
    messages.success(request, f"{structure} removed from its group.")
    return redirect("whatax:admin_groups")
