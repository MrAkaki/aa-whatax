# Hand-authored migration (see 0001_initial.py header).
#
# Adds the standalone read role `view_structures` (§14): drill pop schedule &
# warnings, no payment data. The General model is managed=False (permissions
# anchor only), so this only re-declares Meta.permissions; Django creates the
# new Permission row on migrate. None of the roles imply each other in code.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0009_miningstructure_planned_pop_at"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="general",
            options={
                "default_permissions": (),
                "managed": False,
                "permissions": (
                    ("basic_access", "USER: own dashboard — frags, own mining, own tax record"),
                    ("view_structures", "STRUCTURES: read-only drill pop schedule & warnings (no payments)"),
                    ("manage_payments", "STAFF: fix payments, add/remove balances, view all records"),
                    ("admin_access", "ADMIN: configuration & dangerous actions (keys, rates, exclusions, calc)"),
                ),
            },
        ),
    ]
