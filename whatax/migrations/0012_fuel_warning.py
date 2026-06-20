# Hand-authored migration.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0011_miningstructure_fuel_expires"),
    ]

    operations = [
        migrations.AddField(
            model_name="taxconfiguration",
            name="fuel_warning_days",
            field=models.PositiveSmallIntegerField(
                default=7,
                help_text="DM staff daily once a structure drops below this many days of fuel.",
            ),
        ),
        migrations.AddField(
            model_name="taxconfiguration",
            name="fuel_critical_days",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="At or below this many days, escalate the low-fuel DM to every 6h.",
            ),
        ),
        migrations.AddField(
            model_name="miningstructure",
            name="notified_low_fuel_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Last low-fuel DM time; drives the reminder cadence, cleared on refuel.",
            ),
        ),
        migrations.AddField(
            model_name="miningstructure",
            name="notified_drift_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Set when an off-schedule DM went out; cleared when the pop realigns.",
            ),
        ),
    ]
