"""Forms for the Admin / Staff tabs (TECHNICAL.md §15.2/§15.3).

The Janice key is **write-only**: never rendered back, and a blank submission
keeps the stored value (so the field shows "set / not set", not the secret).
"""

from decimal import Decimal

from django import forms
from eveuniverse.models import EveType

from whatax.models import (
    BalanceAdjustment,
    CorporationTaxRate,
    GoodOreDefault,
    MoonGroup,
    StructureGoodOre,
    TaxConfiguration,
)

# Asteroid category (ores + their compressed/moon variants) — keeps the ore_type
# pickers to a sane list instead of every EveType in the universe.
_ASTEROID_CATEGORY_ID = 25


def _ore_type_queryset():
    return EveType.objects.filter(eve_group__eve_category_id=_ASTEROID_CATEGORY_ID).order_by("name")


class TaxConfigurationForm(forms.ModelForm):
    janice_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the current key.",
    )

    class Meta:
        model = TaxConfiguration
        fields = [
            "default_tax_rate",
            # payment_corporation is intentionally omitted: it is derived from the
            # wallet token's character corp (set in views.add_wallet_token), never
            # picked by hand. payment_wallet_division is likewise omitted — players
            # can only transfer ISK to the master wallet (division 1, the default).
            "broadcast_webhook_url",
            "janice_api_key",
            "reprocessing_yield",
            "mineral_price_basis",
            "grace_period_days",
            "tax_edit_window_days",
            "exclude_highsec",
            "exclude_lowsec",
            "exclude_nullsec",
            "is_enabled",
        ]

    def clean_janice_api_key(self):
        submitted = self.cleaned_data.get("janice_api_key", "")
        # Blank => keep the existing stored key (write-only field).
        return submitted or self.instance.janice_api_key


class CorporationTaxRateForm(forms.ModelForm):
    class Meta:
        model = CorporationTaxRate
        fields = ["corporation", "tax_rate", "flat_discount", "note"]
        widgets = {
            "corporation": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "tax_rate": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
            "flat_discount": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
            "note": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
        }


class GoodOreDefaultForm(forms.ModelForm):
    """Add an ore to the global good-ore default set (§11)."""

    class Meta:
        model = GoodOreDefault
        fields = ["ore_type"]
        widgets = {"ore_type": forms.Select(attrs={"class": "form-select form-select-sm"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ore_type"].queryset = _ore_type_queryset()


class StructureGoodOreForm(forms.ModelForm):
    """Per-structure good-ore override (include = good here / exclude here)."""

    class Meta:
        model = StructureGoodOre
        fields = ["structure", "ore_type", "include"]
        widgets = {
            "structure": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "ore_type": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "include": forms.Select(
                choices=((True, "Good here"), (False, "Excluded here")),
                attrs={"class": "form-select form-select-sm"},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ore_type"].queryset = _ore_type_queryset()


class MoonGroupForm(forms.ModelForm):
    """Create / edit a moon group (name + pop cadence in days)."""

    class Meta:
        model = MoonGroup
        fields = ["name", "schedule_interval_days"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "schedule_interval_days": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "min": 1}
            ),
        }


class BalanceAdjustmentForm(forms.ModelForm):
    class Meta:
        model = BalanceAdjustment
        fields = ["amount", "reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 2})}


class OffWalletPaymentForm(forms.Form):
    """Record a payment a player made outside the corp wallet (§10).

    Booked as a positive ``BalanceAdjustment`` (a credit); the staff comment
    becomes its ``reason`` so the off-wallet payment is auditable.
    """

    amount = forms.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "placeholder": "Amount"}
        ),
    )
    comment = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "class": "form-control form-control-sm",
                "placeholder": "How they paid / note",
            }
        ),
    )


class TaxEditForm(forms.Form):
    new_tax_due = forms.DecimalField(max_digits=20, decimal_places=2, min_value=0)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class WaiveForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
