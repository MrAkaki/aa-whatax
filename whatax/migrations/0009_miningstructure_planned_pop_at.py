# Hand-authored migration (see 0001_initial.py header).
#
# Planned pop (§5.1): a nullable, denormalized projection on MiningStructure.
# planned_pop_at = the structure's soonest still-future chunk_arrival_time (the
# live "next pop") + its group's schedule_interval_days — i.e. the pop after the
# next one. Nullable because ungrouped structures (no cadence) and structures
# with nothing scheduled have no projection. Recomputed in app code wherever the
# inputs change (extraction sync, pop application, group reassignment); existing
# rows backfill to NULL and are filled on the next such event.
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
