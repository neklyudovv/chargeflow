from django.db import migrations, models

import payments.domain.models


def backfill_gateway_fields(apps, schema_editor):
    PaymentAttempt = apps.get_model("payments", "PaymentAttempt")
    WebhookEvent = apps.get_model("payments", "WebhookEvent")
    alias = schema_editor.connection.alias

    for payment in PaymentAttempt.objects.using(alias).select_related(
        "invoice__subscription__customer"
    ).iterator():
        customer = payment.invoice.subscription.customer
        payment.provider = "mock"
        payment.provider_idempotency_key = f"legacy-payment-{payment.pk}"
        payment.currency = payment.invoice.currency
        payment.provider_customer_id = customer.provider_customer_id
        payment.provider_payment_method_id = customer.provider_payment_method_id
        payment.save(
            update_fields=[
                "provider",
                "provider_idempotency_key",
                "currency",
                "provider_customer_id",
                "provider_payment_method_id",
            ]
        )

    for webhook in WebhookEvent.objects.using(alias).iterator():
        webhook.provider = "mock"
        webhook.provider_event_id = f"legacy-webhook-{webhook.pk}"
        webhook.save(update_fields=["provider", "provider_event_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_customer_provider_tokens"),
        ("payments", "0004_payment_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentattempt",
            name="currency",
            field=models.CharField(max_length=3, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="provider_charge_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="provider_customer_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="provider_idempotency_key",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="provider_payment_method_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="webhookevent",
            name="provider",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="webhookevent",
            name="provider_event_id",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.CreateModel(
            name="MockChargeReceipt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("provider_idempotency_key", models.CharField(max_length=255, unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("currency", models.CharField(max_length=3)),
                ("provider_customer_id", models.CharField(blank=True, default="", max_length=255)),
                ("provider_payment_method_id", models.CharField(blank=True, default="", max_length=255)),
                ("success", models.BooleanField()),
                ("provider_charge_id", models.CharField(max_length=255, unique=True)),
                ("provider_response", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.RunPython(backfill_gateway_fields, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="paymentattempt",
            name="currency",
            field=models.CharField(default="USD", max_length=3),
        ),
        migrations.AlterField(
            model_name="paymentattempt",
            name="provider",
            field=models.CharField(default="mock", max_length=50),
        ),
        migrations.AlterField(
            model_name="paymentattempt",
            name="provider_idempotency_key",
            field=models.CharField(
                default=payments.domain.models.new_provider_idempotency_key,
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="webhookevent",
            name="provider",
            field=models.CharField(default="mock", max_length=50),
        ),
        migrations.AlterField(
            model_name="webhookevent",
            name="provider_event_id",
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.UniqueConstraint(
                fields=("provider", "provider_idempotency_key"),
                name="unique_provider_payment_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="webhookevent",
            constraint=models.UniqueConstraint(
                fields=("provider", "provider_event_id"),
                name="unique_provider_event",
            ),
        ),
    ]
