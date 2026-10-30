import hashlib
import json
from decimal import Decimal

from django.db import migrations, models


def fingerprint_mock_requests(apps, schema_editor):
    MockChargeReceipt = apps.get_model("payments", "MockChargeReceipt")
    alias = schema_editor.connection.alias

    for receipt in MockChargeReceipt.objects.using(alias).iterator():
        payload = json.dumps(
            {
                "amount": str(Decimal(receipt.amount).quantize(Decimal("0.01"))),
                "currency": receipt.currency,
                "provider_customer_id": receipt.provider_customer_id,
                "provider_payment_method_id": receipt.provider_payment_method_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        receipt.request_fingerprint = hashlib.sha256(payload).hexdigest()
        receipt.amount = Decimal("0.00")
        receipt.currency = ""
        receipt.provider_customer_id = ""
        receipt.provider_payment_method_id = ""
        receipt.save(
            update_fields=[
                "request_fingerprint",
                "amount",
                "currency",
                "provider_customer_id",
                "provider_payment_method_id",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0005_payment_gateway"),
    ]

    operations = [
        migrations.AddField(
            model_name="mockchargereceipt",
            name="request_fingerprint",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(fingerprint_mock_requests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mockchargereceipt",
            name="request_fingerprint",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="mockchargereceipt",
            name="amount",
            field=models.DecimalField(
                db_default="0.00", decimal_places=2, max_digits=10
            ),
        ),
        migrations.AlterField(
            model_name="mockchargereceipt",
            name="currency",
            field=models.CharField(db_default="", max_length=3),
        ),
        migrations.AlterField(
            model_name="mockchargereceipt",
            name="provider_customer_id",
            field=models.CharField(db_default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="mockchargereceipt",
            name="provider_payment_method_id",
            field=models.CharField(db_default="", max_length=255),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="mockchargereceipt",
                    name="amount",
                ),
                migrations.RemoveField(
                    model_name="mockchargereceipt",
                    name="currency",
                ),
                migrations.RemoveField(
                    model_name="mockchargereceipt",
                    name="provider_customer_id",
                ),
                migrations.RemoveField(
                    model_name="mockchargereceipt",
                    name="provider_payment_method_id",
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.UniqueConstraint(
                condition=~models.Q(provider_charge_id=""),
                fields=("provider", "provider_charge_id"),
                name="unique_provider_charge",
            ),
        ),
    ]
