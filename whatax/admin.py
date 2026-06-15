"""Django admin registrations.

Officer-facing workflows live in the app's own tabbed views (TECHNICAL.md §15);
admin is a low-level inspection/support fallback only. The Janice API key is
never exposed here (write-only secret, §5.1 security note).
"""

from django.contrib import admin

from whatax import models


@admin.register(models.TaxConfiguration)
class TaxConfigurationAdmin(admin.ModelAdmin):
    # Deliberately exclude janice_api_key from list and detail views.
    exclude = ("janice_api_key",)
    list_display = ("__str__", "default_tax_rate", "mineral_price_basis", "is_enabled")


@admin.register(models.CorporationTaxRate)
class CorporationTaxRateAdmin(admin.ModelAdmin):
    list_display = ("corporation", "tax_rate", "flat_discount", "note")


@admin.register(models.MiningStructure)
class MiningStructureAdmin(admin.ModelAdmin):
    list_display = ("name", "structure_id", "corporation", "is_active", "last_ledger_sync", "planned_pop_at")
    list_filter = ("is_active", "corporation")


@admin.register(models.TaxPeriod)
class TaxPeriodAdmin(admin.ModelAdmin):
    list_display = ("__str__", "state", "calculated_at")
    list_filter = ("state",)


@admin.register(models.TaxRecord)
class TaxRecordAdmin(admin.ModelAdmin):
    list_display = ("tax_period", "user", "tax_due", "amount_paid", "status")
    list_filter = ("status", "tax_period")
    raw_id_fields = ("user", "corporation_at_calc", "tax_period")


@admin.register(models.WalletJournalEntry)
class WalletJournalEntryAdmin(admin.ModelAdmin):
    list_display = ("entry_id", "ref_type", "amount", "date", "is_processed")
    list_filter = ("is_processed", "ref_type")


@admin.register(models.Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("amount", "user", "tax_record", "match_method", "date")
    list_filter = ("match_method",)
    raw_id_fields = ("user", "tax_record", "journal_entry")


@admin.register(models.MoonExtraction)
class MoonExtractionAdmin(admin.ModelAdmin):
    list_display = ("structure", "eve_moon", "chunk_arrival_time", "status")
    list_filter = ("status",)


admin.site.register(models.MiningSnapshot)
admin.site.register(models.BalanceAdjustment)
admin.site.register(models.TaxRecordEdit)
admin.site.register(models.PlayerNotificationPref)
