# Hand-authored migration (see 0001_initial.py header).
#
# Fuel tracking: a nullable timestamp on MiningStructure mirroring the
# `fuel_expires` field EVE's corp-structures endpoint already returns (the moment
# fuel runs out, computed by EVE from the live fit and fuel-bay contents). Stored
# by sync_structures; the model's `fuel_days_left` property derives days remaining
# for the structure tables. Nullable because the field is unknown until the next
# sync; existing rows backfill to NULL and fill on the next sync_structures run.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0010_view_structures_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="miningstructure",
            name="fuel_expires",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="ESI-reported moment fuel runs out; None when unknown.",
            ),
        ),
    ]
