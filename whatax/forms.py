"""Forms for the Admin / Staff tabs. The Janice key is write-only."""

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

# Asteroid category (ores + their compressed/moon variants) for ore_type pickers.
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
            # payment_corporation and payment_wallet_division are omitted: derived,
            # not picked by hand.
            "broadcast_webhook_url",
            "janice_api_key",
            "reprocessing_yield",
            "mineral_price_basis",
            "grace_period_days",
            "tax_edit_window_days",
            "exclude_highsec",
            "exclude_lowsec",
            "exclude_nullsec",
            "allowed_group",
            "is_enabled",
        ]
        widgets = {
            "allowed_group": forms.Select(attrs={"class": "form-select form-select-sm"}),
        }

    def clean_janice_api_key(self):
        submitted = self.cleaned_data.get("janice_api_key", "")
        # Blank keeps the existing stored key.
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
    """Add an ore to the global good-ore default set."""

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
    """Record a payment a player made outside the corp wallet."""

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


class KosCharacterForm(forms.Form):
    """Add a kill-on-sight character by name; the view resolves it via ESI."""

    character_name = forms.CharField(
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": "Character name"}
        ),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": "Reason (optional)"}
        ),
    )


class TaxEditForm(forms.Form):
    new_tax_due = forms.DecimalField(max_digits=20, decimal_places=2, min_value=0)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class WaiveForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
