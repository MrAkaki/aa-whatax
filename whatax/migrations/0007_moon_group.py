# Hand-authored migration (see 0001_initial.py header).
#
# Moon groups (§5.1): a MoonGroup bundles mining structures popped on a shared
# cadence (schedule_interval_days). MiningStructure.group is a nullable FK — a
# moon belongs to at most one group, a freshly-synced structure to none, and
# reassigning the FK moves it out of any previous group. SET_NULL so deleting a
# group leaves its structures intact (just ungrouped). The interval is stored
# now and consumed later to project moon pop times.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0006_good_ore_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="MoonGroup",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                (
                    "schedule_interval_days",
                    models.PositiveSmallIntegerField(
                        help_text="Days between scheduled pops for this group's moons."
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="miningstructure",
            name="group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="structures",
                to="whatax.moongroup",
            ),
        ),
    ]
