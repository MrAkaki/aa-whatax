# Hand-authored migration.
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
