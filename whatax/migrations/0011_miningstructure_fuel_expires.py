# Hand-authored migration.
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
