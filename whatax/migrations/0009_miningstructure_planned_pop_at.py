# Hand-authored migration.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0008_alter_balanceadjustment_amount_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="miningstructure",
            name="planned_pop_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Projected next-cycle pop: next pop + the group's schedule_interval_days.",
            ),
        ),
    ]
