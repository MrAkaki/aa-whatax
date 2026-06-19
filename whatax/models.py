"""Data model."""

import datetime as dt
from decimal import Decimal

from allianceauth.eveonline.models import EveCorporationInfo
from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _
from eveuniverse.models import EveMoon, EveSolarSystem, EveType

from whatax import app_settings
from whatax.core.timeutils import eve_now
from whatax.managers import (
    MiningSnapshotManager,
    MiningStructureManager,
    TaxConfigurationManager,
    TaxRecordManager,
)

# Decimal field shapes.
_MONEY = dict(max_digits=20, decimal_places=2)
_RATE = dict(max_digits=5, decimal_places=4)
_VOLUME = dict(max_digits=24, decimal_places=2)


class General(models.Model):
    """Permissions anchor only — not a real table."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "USER: own dashboard — frags, own mining, own tax record"),
            ("view_structures", "STRUCTURES: read-only drill pop schedule & warnings (no payments)"),
            ("manage_payments", "STAFF: fix payments, add/remove balances, view all records"),
            ("admin_access", "ADMIN: configuration & dangerous actions (keys, rates, exclusions, calc)"),
        )


class TaxConfiguration(models.Model):
    """Singleton (pk=1) — this row *is* the app's config. Edited in the Admin tab."""

    class PriceBasis(models.TextChoices):
        SPLIT_IMMEDIATE = "split_immediate", _("Jita split (immediate)")
        BUY_IMMEDIATE = "buy_immediate", _("Jita buy (immediate)")
        SELL_IMMEDIATE = "sell_immediate", _("Jita sell (immediate)")
        SPLIT_TOP5 = "split_top5", _("Jita split (top 5% average)")
        BUY_TOP5 = "buy_top5", _("Jita buy (top 5% average)")
        SELL_TOP5 = "sell_top5", _("Jita sell (top 5% average)")

    default_tax_rate = models.DecimalField(
        default=Decimal("0.1000"), help_text=_("Global rate; per-corp overrides win."), **_RATE
    )
    payment_corporation = models.ForeignKey(
        EveCorporationInfo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Corp players pay to; its wallet is monitored."),
    )
    payment_wallet_division = models.PositiveSmallIntegerField(default=1, help_text=_("1–7."))
    broadcast_webhook_url = models.URLField(blank=True, max_length=500)
    janice_api_key = models.CharField(
        max_length=255, blank=True, help_text=_("Stored in DB; never rendered back.")
    )
    # Pricing controls; recorded on each snapshot.
    reprocessing_yield = models.DecimalField(
        default=app_settings.REPROCESSING_YIELD_DEFAULT,
        help_text=_("Refined-value efficiency factor, e.g. 0.906."),
        **_RATE,
    )
    mineral_price_basis = models.CharField(
        max_length=20,
        choices=PriceBasis.choices,
        default=app_settings.MINERAL_PRICE_BASIS_DEFAULT,
        help_text=_("Which Janice market figure values minerals."),
    )
    grace_period_days = models.PositiveSmallIntegerField(
        default=14, help_text=_("Pay-by window after emission before overdue.")
    )
    tax_edit_window_days = models.PositiveSmallIntegerField(
        default=15, help_text=_("Days after emission during which staff may edit tax_due.")
    )
    exclude_highsec = models.BooleanField(default=False)
    exclude_lowsec = models.BooleanField(default=False)
    exclude_nullsec = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=False, help_text=_("Master kill-switch for scheduled work."))

    objects = TaxConfigurationManager()

    class Meta:
        verbose_name = _("tax configuration")

    def __str__(self):
        return "Whale Tax configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)


class RegisteredToken(models.Model):
    """An ESI token granted through whatax's Admin tab."""

    class Purpose(models.TextChoices):
        STRUCTURES = "structures", _("Structures & moons")
        WALLET = "wallet", _("Payment wallet")

    token = models.OneToOneField(
        "esi.Token", on_delete=models.CASCADE, related_name="whatax_registration"
    )
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_purpose_display()}: {self.token_id}"


class CorporationTaxRate(models.Model):
    """Per-corp override; applies to players whose **main** char is in this corp."""

    corporation = models.OneToOneField(
        EveCorporationInfo, on_delete=models.PROTECT, related_name="whatax_tax_rate"
    )
    tax_rate = models.DecimalField(**_RATE)
    flat_discount = models.DecimalField(
        default=Decimal("0"),
        help_text=_("ISK subtracted from each member's monthly charge; the charge floors at 0."),
        **_MONEY,
    )
    note = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.corporation}: {self.tax_rate}"


class MoonGroup(models.Model):
    """A named set of mining structures popped on a shared schedule."""

    name = models.CharField(max_length=100, unique=True)
    schedule_interval_days = models.PositiveSmallIntegerField(
        help_text=_("Days between scheduled pops for this group's moons.")
    )

    def __str__(self):
        return self.name


class MiningStructure(models.Model):
    """A corp-owned refinery/drill (an ESI mining *observer*)."""

    structure_id = models.BigIntegerField(unique=True, help_text=_("ESI structure id; doubles as observer_id."))
    corporation = models.ForeignKey(EveCorporationInfo, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=255, blank=True)
    eve_solar_system = models.ForeignKey(
        EveSolarSystem, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    eve_type = models.ForeignKey(EveType, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    is_active = models.BooleanField(
        default=True, help_text=_("False excludes this structure's mining from tax.")
    )
    group = models.ForeignKey(
        MoonGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="structures"
    )
    last_ledger_sync = models.DateTimeField(null=True, blank=True)
    planned_pop_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Projected next-cycle pop: next pop + the group's schedule_interval_days."),
    )
    fuel_expires = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("ESI-reported moment fuel runs out; None when unknown."),
    )

    objects = MiningStructureManager()

    def __str__(self):
        return self.name or str(self.structure_id)

    @property
    def fuel_days_left(self):
        """Whole days until fuel runs out, or None when unknown/expired-past."""
        if self.fuel_expires is None:
            return None
        delta = self.fuel_expires - eve_now()
        return max(0, delta.days)  # floor; 0 means <1 day left

    def recompute_planned_pop(self, *, accept: bool = False, save: bool = True):
        """Refresh sticky ``planned_pop_at`` from the live next pop + group cadence."""
        next_pop = None
        if self.group_id is not None:
            next_pop = (
                self.extractions.filter(chunk_arrival_time__gte=eve_now())
                .exclude(status__in=MoonExtraction.TERMINAL_STATUSES)
                .aggregate(models.Min("chunk_arrival_time"))["chunk_arrival_time__min"]
            )
        if self.group_id is None:
            planned = None
        elif next_pop is None:
            # Pop fired but next cycle not scheduled yet: keep standing projection.
            planned = self.planned_pop_at
        elif (
            accept
            or self.planned_pop_at is None
            or self.planned_pop_at.date() == next_pop.date()
        ):
            planned = next_pop + dt.timedelta(days=self.group.schedule_interval_days)
        else:
            # Off-schedule reset: keep projection so the deviation stays visible.
            planned = self.planned_pop_at
        self.planned_pop_at = planned
        if save:
            self.save(update_fields=["planned_pop_at"])
        return planned


class GoodOreDefault(models.Model):
    """Global good-ore set: ores that count at every structure by default."""

    ore_type = models.OneToOneField(EveType, on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return str(self.ore_type)


class StructureGoodOre(models.Model):
    """Per-structure override of the global good-ore set (dead-detection)."""

    structure = models.ForeignKey(MiningStructure, on_delete=models.CASCADE, related_name="good_ores")
    ore_type = models.ForeignKey(EveType, on_delete=models.PROTECT, related_name="+")
    include = models.BooleanField(
        default=True, help_text=_("True = add this ore as good here; False = exclude it here.")
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["structure", "ore_type"], name="whatax_good_ore_unique")
        ]

    def __str__(self):
        verb = "good" if self.include else "excluded"
        return f"{self.structure}: {self.ore_type} ({verb})"


class TaxPeriod(models.Model):
    """One calendar month."""

    class State(models.TextChoices):
        OPEN = "open", _("Open")
        CALCULATING = "calculating", _("Calculating")
        FINALIZED = "finalized", _("Finalized")
        CLOSED = "closed", _("Closed")

    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    calculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="whatax_period_unique")
        ]
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.year}-{self.month:02d}"


class MiningLedgerEntry(models.Model):
    """Raw observer rows — the single source of truth for mining."""

    structure = models.ForeignKey(MiningStructure, on_delete=models.CASCADE, related_name="ledger_entries")
    character_id = models.BigIntegerField(db_index=True)
    ore_type = models.ForeignKey(EveType, on_delete=models.PROTECT, related_name="+")
    quantity = models.BigIntegerField()
    recorded_date = models.DateField(help_text=_("Observer last_updated (a date)."))
    recorded_corporation_id = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "character_id", "ore_type", "recorded_date"],
                name="whatax_ledger_unique",
            )
        ]
        indexes = [models.Index(fields=["recorded_date"], name="whatax_mle_recdate_idx")]

    def __str__(self):
        return f"{self.character_id} {self.ore_type} x{self.quantity} @ {self.recorded_date}"


class MiningSnapshot(models.Model):
    """Aggregated mining per player/ore/period (derived from ledger entries)."""

    tax_period = models.ForeignKey(TaxPeriod, on_delete=models.CASCADE, related_name="snapshots")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    ore_type = models.ForeignKey(EveType, on_delete=models.PROTECT, related_name="+")
    quantity = models.BigIntegerField()
    refined_value = models.DecimalField(default=Decimal("0"), **_MONEY)
    # Freeze the pricing inputs used at calc time.
    reprocessing_yield_applied = models.DecimalField(null=True, blank=True, **_RATE)
    price_basis_applied = models.CharField(max_length=20, blank=True)
    is_excluded = models.BooleanField(
        default=False, help_text=_("Mining counted but not taxed (sec-class / structure exclusion).")
    )

    objects = MiningSnapshotManager()

    class Meta:
        constraints = [
            # is_excluded is part of the key: one row per taxed/excluded bucket.
            models.UniqueConstraint(
                fields=["tax_period", "user", "ore_type", "is_excluded"],
                name="whatax_snapshot_unique",
            )
        ]

    def __str__(self):
        return f"{self.tax_period} {self.user} {self.ore_type} x{self.quantity}"


class TaxRecord(models.Model):
    """The bill, per player/period."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PARTIAL = "partial", _("Partial")
        PAID = "paid", _("Paid")
        WAIVED = "waived", _("Waived")
        OVERDUE = "overdue", _("Overdue")

    tax_period = models.ForeignKey(TaxPeriod, on_delete=models.CASCADE, related_name="tax_records")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="whatax_tax_records")
    total_mined_value = models.DecimalField(default=Decimal("0"), **_MONEY)
    tax_rate_applied = models.DecimalField(default=Decimal("0"), **_RATE)
    original_tax_due = models.DecimalField(
        default=Decimal("0"), help_text=_("Immutable audit baseline at emission (post-discount)."), **_MONEY
    )
    flat_discount_applied = models.DecimalField(
        default=Decimal("0"),
        help_text=_("Corp flat discount subtracted at calc; recorded for transparency."),
        **_MONEY,
    )
    tax_due = models.DecimalField(
        default=Decimal("0"), help_text=_("Effective charge (may differ after a staff edit)."), **_MONEY
    )
    amount_paid = models.DecimalField(default=Decimal("0"), help_text=_("Σ matched payments."), **_MONEY)
    emitted_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    corporation_at_calc = models.ForeignKey(
        EveCorporationInfo, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    notified_due_at = models.DateTimeField(null=True, blank=True)

    objects = TaxRecordManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tax_period", "user"], name="whatax_record_unique")
        ]
        ordering = ["-tax_period__year", "-tax_period__month"]

    def __str__(self):
        return f"{self.tax_period} {self.user}: {self.tax_due}"

    @property
    def adjustments_total(self) -> Decimal:
        return sum((a.amount for a in self.adjustments.all()), Decimal("0"))

    @property
    def settled(self) -> Decimal:
        """Amount counted toward the bill: payments + manual adjustments."""
        return self.amount_paid + self.adjustments_total

    @property
    def balance(self) -> Decimal:
        """Signed balance: negative = owed, 0 = settled, positive = credit."""
        return self.settled - self.tax_due


class WalletJournalEntry(models.Model):
    """Raw corp wallet journal rows."""

    entry_id = models.BigIntegerField(unique=True, help_text=_("ESI journal id (primary idempotency key)."))
    division = models.PositiveSmallIntegerField()
    ref_type = models.CharField(max_length=64)
    amount = models.DecimalField(**_MONEY)
    balance = models.DecimalField(null=True, blank=True, **_MONEY)
    date = models.DateTimeField()
    first_party_id = models.BigIntegerField(null=True, blank=True)
    second_party_id = models.BigIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True)
    is_processed = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["is_processed"], name="whatax_wje_proc_idx"),
            models.Index(fields=["date"], name="whatax_wje_date_idx"),
        ]

    def __str__(self):
        return f"journal {self.entry_id}: {self.amount}"


class Payment(models.Model):
    """A matched (or unmatched) inflow attributable to a player."""

    class MatchMethod(models.TextChoices):
        AUTO = "auto", _("Auto")
        MANUAL = "manual", _("Manual")
        UNMATCHED = "unmatched", _("Unmatched")

    journal_entry = models.OneToOneField(
        WalletJournalEntry, on_delete=models.PROTECT, related_name="payment"
    )
    tax_record = models.ForeignKey(
        TaxRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments"
    )
    character_id = models.BigIntegerField(null=True, blank=True, help_text=_("Resolved payer (= first_party_id)."))
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    amount = models.DecimalField(**_MONEY)
    date = models.DateTimeField()
    match_method = models.CharField(max_length=10, choices=MatchMethod.choices, default=MatchMethod.UNMATCHED)
    notified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"payment {self.amount} ({self.match_method})"


class BalanceAdjustment(models.Model):
    """Staff manual credit/debit on a record (kept separate from Payment)."""

    tax_record = models.ForeignKey(TaxRecord, on_delete=models.CASCADE, related_name="adjustments")
    amount = models.DecimalField(help_text=_("Signed: + credit (reduces owed), − debit."), **_MONEY)
    reason = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"adjust {self.amount} on {self.tax_record_id}"


class TaxRecordEdit(models.Model):
    """Audit log for a staff tax-amount correction (changes tax_due, not payments)."""

    tax_record = models.ForeignKey(TaxRecord, on_delete=models.CASCADE, related_name="edits")
    old_tax_due = models.DecimalField(**_MONEY)
    new_tax_due = models.DecimalField(**_MONEY)
    reason = models.TextField()
    edited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    edited_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"edit {self.old_tax_due}->{self.new_tax_due} on {self.tax_record_id}"


class MoonExtraction(models.Model):
    """One extraction cycle for a structure (a.k.a. MoonCycle)."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        ACTIVE = "active", _("Active")
        POPPED = "popped", _("Popped")
        DEAD = "dead", _("Dead")
        CANCELLED = "cancelled", _("Cancelled")

    # Statuses past which an extraction is no longer pending/upcoming.
    TERMINAL_STATUSES = {Status.POPPED, Status.DEAD, Status.CANCELLED}

    structure = models.ForeignKey(MiningStructure, on_delete=models.CASCADE, related_name="extractions")
    eve_moon = models.ForeignKey(EveMoon, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    extraction_start_time = models.DateTimeField()
    chunk_arrival_time = models.DateTimeField()
    natural_decay_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    popped_at = models.DateTimeField(null=True, blank=True)
    total_good_ore_m3 = models.DecimalField(
        null=True, blank=True, help_text=_("Dead-% denominator; NULL = composition unknown."), **_VOLUME
    )
    mined_good_ore_m3 = models.DecimalField(default=Decimal("0"), **_VOLUME)
    dead_at = models.DateTimeField(null=True, blank=True)
    notified_pop_at = models.DateTimeField(null=True, blank=True)
    notified_dead_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "chunk_arrival_time"], name="whatax_extraction_unique"
            )
        ]
        ordering = ["-chunk_arrival_time"]

    def __str__(self):
        return f"{self.structure} chunk @ {self.chunk_arrival_time}"

    @property
    def dead_fraction(self) -> Decimal | None:
        if not self.total_good_ore_m3:
            return None
        return self.mined_good_ore_m3 / self.total_good_ore_m3


class ExtractionOre(models.Model):
    """Per-extraction ore composition, snapshotted from the started-notification."""

    extraction = models.ForeignKey(MoonExtraction, on_delete=models.CASCADE, related_name="ores")
    ore_type = models.ForeignKey(EveType, on_delete=models.PROTECT, related_name="+")
    volume_m3 = models.DecimalField(**_VOLUME)
    is_good_ore = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["extraction", "ore_type"], name="whatax_extraction_ore_unique")
        ]

    def __str__(self):
        return f"{self.extraction}: {self.ore_type} {self.volume_m3} m³"


class PlayerNotificationPref(models.Model):
    """Opt-in Discord DM preference per player."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="whatax_notification_pref")
    dm_opt_in = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user}: dm={self.dm_opt_in}"


class ProcessedNotification(models.Model):
    """Idempotency ledger for ESI corp notifications already applied."""

    notification_id = models.BigIntegerField()
    notification_type = models.CharField(max_length=64)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification_id"], name="whatax_processed_notification_unique"
            )
        ]

    def __str__(self):
        return f"{self.notification_type}#{self.notification_id}"
