# Hand-authored migration (see 0001_initial.py header).
#
# Good-ore set for moon dead-detection becomes a global default + per-structure
# override (§11). GoodOreDefault is the global list (seeded with all moon ores by
# the whatax_seed_good_ores command); StructureGoodOre.include flips a row between
# an add ("good here even if not a global default") and an exclude ("not good
# here"). The effective set is resolved in core.moons.good_ore_ids_for.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("eveuniverse", "__first__"),
        ("whatax", "0005_flat_discount"),
    ]

    operations = [
        migrations.CreateModel(
            name="GoodOreDefault",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "ore_type",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="eveuniverse.evetype",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="structuregoodore",
            name="include",
            field=models.BooleanField(
                default=True,
                help_text="True = add this ore as good here; False = exclude it here.",
            ),
        ),
    ]
