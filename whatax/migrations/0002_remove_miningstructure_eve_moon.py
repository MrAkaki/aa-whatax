# Hand-authored migration (see 0001_initial.py header).
#
# Drops MiningStructure.eve_moon: the corp structures endpoint never provided a
# moon_id so the column was always null, and the staff UI column was removed.
# Moon data still lives on MoonExtraction.eve_moon, which is unaffected.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="miningstructure",
            name="eve_moon",
        ),
    ]
