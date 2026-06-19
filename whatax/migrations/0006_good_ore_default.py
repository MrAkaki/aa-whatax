# Hand-authored migration.
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
