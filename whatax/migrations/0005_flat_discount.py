# Hand-authored migration (see 0001_initial.py header).
#
# Adds per-corp flat ISK discounts: CorporationTaxRate.flat_discount is the
# amount subtracted from each member's monthly charge (floored at 0), and
# TaxRecord.flat_discount_applied freezes the portion actually used at calc time
# so an emitted bill reconciles as (total × rate) − flat_discount_applied (§9).
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("whatax", "0004_processednotification"),
    ]

    operations = [
        migrations.AddField(
            model_name="corporationtaxrate",
            name="flat_discount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="ISK subtracted from each member's monthly charge; the charge floors at 0.",
                max_digits=20,
            ),
        ),
        migrations.AddField(
            model_name="taxrecord",
            name="flat_discount_applied",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Corp flat discount subtracted at calc; recorded for transparency (§9).",
                max_digits=20,
            ),
        ),
    ]
