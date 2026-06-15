# Hand-authored migration (see 0001_initial.py header).
#
# Adds ProcessedNotification: an idempotency ledger keyed on the ESI
# notification_id so the rolling notifications window (replayed every poll, and
# possibly seen via several corp tokens) applies each moon-pop event — and fires
# its Discord notification — at most once. The unique constraint is the race
# guard: a concurrent double-poll loses on insert instead of double-popping.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0003_registeredtoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProcessedNotification",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notification_id", models.BigIntegerField()),
                ("notification_type", models.CharField(max_length=64)),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="processednotification",
            constraint=models.UniqueConstraint(
                fields=("notification_id",), name="whatax_processed_notification_unique"
            ),
        ),
    ]
