# Hand-authored migration.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "__first__"),
        ("eveuniverse", "__first__"),
        ("whatax", "0012_fuel_warning"),
    ]

    operations = [
        migrations.AddField(
            model_name="taxconfiguration",
            name="allowed_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="auth.group",
                help_text=(
                    "Members of this group are the 'allowed characters' shown on "
                    "the Characters tab."
                ),
            ),
        ),
        migrations.CreateModel(
            name="KosCharacter",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("added_at", models.DateTimeField(auto_now_add=True)),
                (
                    "added_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "character",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="eveuniverse.eveentity",
                    ),
                ),
            ],
            options={
                "ordering": ["character__name"],
            },
        ),
    ]
