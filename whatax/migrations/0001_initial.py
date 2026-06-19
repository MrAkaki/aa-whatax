# Hand-authored migration.
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import whatax.app_settings

_MONEY = dict(max_digits=20, decimal_places=2)
_RATE = dict(max_digits=5, decimal_places=4)
_VOLUME = dict(max_digits=24, decimal_places=2)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("eveonline", "__first__"),
        ("eveuniverse", "__first__"),
    ]

    operations = [
        migrations.CreateModel(
            name="General",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ],
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": (
                    ("basic_access", "USER: own dashboard — frags, own mining, own tax record"),
                    ("manage_payments", "STAFF: fix payments, add/remove balances, view all records"),
                    ("admin_access", "ADMIN: configuration & dangerous actions (keys, rates, exclusions, calc)"),
                ),
            },
        ),
        migrations.CreateModel(
            name="TaxConfiguration",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("default_tax_rate", models.DecimalField(default=Decimal("0.1000"), **_RATE)),
                ("payment_wallet_division", models.PositiveSmallIntegerField(default=1)),
                ("broadcast_webhook_url", models.URLField(blank=True, max_length=500)),
                ("janice_api_key", models.CharField(blank=True, max_length=255)),
                ("reprocessing_yield", models.DecimalField(default=whatax.app_settings.REPROCESSING_YIELD_DEFAULT, **_RATE)),
                ("mineral_price_basis", models.CharField(default=whatax.app_settings.MINERAL_PRICE_BASIS_DEFAULT, max_length=20, choices=[("split_immediate", "Jita split (immediate)"), ("buy_immediate", "Jita buy (immediate)"), ("sell_immediate", "Jita sell (immediate)"), ("split_top5", "Jita split (top 5% average)"), ("buy_top5", "Jita buy (top 5% average)"), ("sell_top5", "Jita sell (top 5% average)")])),
                ("grace_period_days", models.PositiveSmallIntegerField(default=14)),
                ("tax_edit_window_days", models.PositiveSmallIntegerField(default=15)),
                ("exclude_highsec", models.BooleanField(default=False)),
                ("exclude_lowsec", models.BooleanField(default=False)),
                ("exclude_nullsec", models.BooleanField(default=False)),
                ("is_enabled", models.BooleanField(default=False)),
                ("payment_corporation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveonline.evecorporationinfo")),
            ],
            options={"verbose_name": "tax configuration"},
        ),
        migrations.CreateModel(
            name="CorporationTaxRate",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tax_rate", models.DecimalField(**_RATE)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("corporation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="whatax_tax_rate", to="eveonline.evecorporationinfo")),
            ],
        ),
        migrations.CreateModel(
            name="MiningStructure",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("structure_id", models.BigIntegerField(unique=True)),
                ("name", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("last_ledger_sync", models.DateTimeField(blank=True, null=True)),
                ("corporation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveonline.evecorporationinfo")),
                ("eve_moon", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evemoon")),
                ("eve_solar_system", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evesolarsystem")),
                ("eve_type", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evetype")),
            ],
        ),
        migrations.CreateModel(
            name="StructureGoodOre",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ore_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evetype")),
                ("structure", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="good_ores", to="whatax.miningstructure")),
            ],
        ),
        migrations.CreateModel(
            name="TaxPeriod",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("year", models.PositiveSmallIntegerField()),
                ("month", models.PositiveSmallIntegerField()),
                ("period_start", models.DateTimeField()),
                ("period_end", models.DateTimeField()),
                ("state", models.CharField(default="open", max_length=12, choices=[("open", "Open"), ("calculating", "Calculating"), ("finalized", "Finalized"), ("closed", "Closed")])),
                ("calculated_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-year", "-month"]},
        ),
        migrations.CreateModel(
            name="MiningLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("character_id", models.BigIntegerField(db_index=True)),
                ("quantity", models.BigIntegerField()),
                ("recorded_date", models.DateField()),
                ("recorded_corporation_id", models.BigIntegerField()),
                ("ore_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evetype")),
                ("structure", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ledger_entries", to="whatax.miningstructure")),
            ],
            options={"indexes": [models.Index(fields=["recorded_date"], name="whatax_mle_recdate_idx")]},
        ),
        migrations.CreateModel(
            name="MiningSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.BigIntegerField()),
                ("refined_value", models.DecimalField(default=Decimal("0"), **_MONEY)),
                ("reprocessing_yield_applied", models.DecimalField(blank=True, null=True, **_RATE)),
                ("price_basis_applied", models.CharField(blank=True, max_length=20)),
                ("is_excluded", models.BooleanField(default=False)),
                ("ore_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evetype")),
                ("tax_period", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="snapshots", to="whatax.taxperiod")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="TaxRecord",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("total_mined_value", models.DecimalField(default=Decimal("0"), **_MONEY)),
                ("tax_rate_applied", models.DecimalField(default=Decimal("0"), **_RATE)),
                ("original_tax_due", models.DecimalField(default=Decimal("0"), **_MONEY)),
                ("tax_due", models.DecimalField(default=Decimal("0"), **_MONEY)),
                ("amount_paid", models.DecimalField(default=Decimal("0"), **_MONEY)),
                ("emitted_at", models.DateTimeField(blank=True, null=True)),
                ("due_date", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(default="pending", max_length=10, choices=[("pending", "Pending"), ("partial", "Partial"), ("paid", "Paid"), ("waived", "Waived"), ("overdue", "Overdue")])),
                ("notified_due_at", models.DateTimeField(blank=True, null=True)),
                ("corporation_at_calc", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveonline.evecorporationinfo")),
                ("tax_period", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tax_records", to="whatax.taxperiod")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="whatax_tax_records", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-tax_period__year", "-tax_period__month"]},
        ),
        migrations.CreateModel(
            name="WalletJournalEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_id", models.BigIntegerField(unique=True)),
                ("division", models.PositiveSmallIntegerField()),
                ("ref_type", models.CharField(max_length=64)),
                ("amount", models.DecimalField(**_MONEY)),
                ("balance", models.DecimalField(blank=True, null=True, **_MONEY)),
                ("date", models.DateTimeField()),
                ("first_party_id", models.BigIntegerField(blank=True, null=True)),
                ("second_party_id", models.BigIntegerField(blank=True, null=True)),
                ("reason", models.TextField(blank=True)),
                ("is_processed", models.BooleanField(default=False)),
            ],
            options={"indexes": [models.Index(fields=["is_processed"], name="whatax_wje_proc_idx"), models.Index(fields=["date"], name="whatax_wje_date_idx")]},
        ),
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("character_id", models.BigIntegerField(blank=True, null=True)),
                ("amount", models.DecimalField(**_MONEY)),
                ("date", models.DateTimeField()),
                ("match_method", models.CharField(default="unmatched", max_length=10, choices=[("auto", "Auto"), ("manual", "Manual"), ("unmatched", "Unmatched")])),
                ("notified_at", models.DateTimeField(blank=True, null=True)),
                ("journal_entry", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="payment", to="whatax.walletjournalentry")),
                ("tax_record", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments", to="whatax.taxrecord")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="BalanceAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(**_MONEY)),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("tax_record", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="adjustments", to="whatax.taxrecord")),
            ],
        ),
        migrations.CreateModel(
            name="TaxRecordEdit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("old_tax_due", models.DecimalField(**_MONEY)),
                ("new_tax_due", models.DecimalField(**_MONEY)),
                ("reason", models.TextField()),
                ("edited_at", models.DateTimeField(auto_now_add=True)),
                ("edited_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("tax_record", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="edits", to="whatax.taxrecord")),
            ],
        ),
        migrations.CreateModel(
            name="MoonExtraction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("extraction_start_time", models.DateTimeField()),
                ("chunk_arrival_time", models.DateTimeField()),
                ("natural_decay_time", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(default="scheduled", max_length=10, choices=[("scheduled", "Scheduled"), ("active", "Active"), ("popped", "Popped"), ("dead", "Dead"), ("cancelled", "Cancelled")])),
                ("popped_at", models.DateTimeField(blank=True, null=True)),
                ("total_good_ore_m3", models.DecimalField(blank=True, null=True, **_VOLUME)),
                ("mined_good_ore_m3", models.DecimalField(default=Decimal("0"), **_VOLUME)),
                ("dead_at", models.DateTimeField(blank=True, null=True)),
                ("notified_pop_at", models.DateTimeField(blank=True, null=True)),
                ("notified_dead_at", models.DateTimeField(blank=True, null=True)),
                ("eve_moon", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evemoon")),
                ("structure", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="extractions", to="whatax.miningstructure")),
            ],
            options={"ordering": ["-chunk_arrival_time"]},
        ),
        migrations.CreateModel(
            name="ExtractionOre",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("volume_m3", models.DecimalField(**_VOLUME)),
                ("is_good_ore", models.BooleanField(default=False)),
                ("extraction", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ores", to="whatax.moonextraction")),
                ("ore_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="eveuniverse.evetype")),
            ],
        ),
        migrations.CreateModel(
            name="PlayerNotificationPref",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("dm_opt_in", models.BooleanField(default=False)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="whatax_notification_pref", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="structuregoodore",
            constraint=models.UniqueConstraint(fields=("structure", "ore_type"), name="whatax_good_ore_unique"),
        ),
        migrations.AddConstraint(
            model_name="taxperiod",
            constraint=models.UniqueConstraint(fields=("year", "month"), name="whatax_period_unique"),
        ),
        migrations.AddConstraint(
            model_name="miningledgerentry",
            constraint=models.UniqueConstraint(fields=("structure", "character_id", "ore_type", "recorded_date"), name="whatax_ledger_unique"),
        ),
        migrations.AddConstraint(
            model_name="miningsnapshot",
            constraint=models.UniqueConstraint(fields=("tax_period", "user", "ore_type", "is_excluded"), name="whatax_snapshot_unique"),
        ),
        migrations.AddConstraint(
            model_name="taxrecord",
            constraint=models.UniqueConstraint(fields=("tax_period", "user"), name="whatax_record_unique"),
        ),
        migrations.AddConstraint(
            model_name="moonextraction",
            constraint=models.UniqueConstraint(fields=("structure", "chunk_arrival_time"), name="whatax_extraction_unique"),
        ),
        migrations.AddConstraint(
            model_name="extractionore",
            constraint=models.UniqueConstraint(fields=("extraction", "ore_type"), name="whatax_extraction_ore_unique"),
        ),
    ]
