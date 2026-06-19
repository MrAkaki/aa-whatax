# Hand-authored migration.
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
