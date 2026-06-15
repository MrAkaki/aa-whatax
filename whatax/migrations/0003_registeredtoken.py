# Hand-authored migration (see 0001_initial.py header).
#
# Adds RegisteredToken: whatax now records which esi tokens were granted through
# its own Admin-tab buttons, instead of rescanning the shared esi.Token table by
# scope (which surfaced unrelated tokens on an established AA install).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("esi", "__first__"),
        ("whatax", "0002_remove_miningstructure_eve_moon"),
    ]

    operations = [
        migrations.CreateModel(
            name="RegisteredToken",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purpose", models.CharField(choices=[("structures", "Structures & moons"), ("wallet", "Payment wallet")], max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("token", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="whatax_registration", to="esi.token")),
            ],
        ),
    ]
