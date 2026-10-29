from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Payment = apps.get_model("payments", "PaymentAttempt")
    Webhook = apps.get_model("payments", "WebhookEvent")
    alias = schema_editor.connection.alias
    for row in Payment.objects.using(alias).select_related("invoice").iterator():
        row.organization_id = row.invoice.organization_id
        row.idempotency_key = row.idempotency_key or None
        row.save(update_fields=["organization", "idempotency_key"])
    for row in Webhook.objects.using(alias).iterator():
        payment_id = row.payload.get("payment_attempt_id") if isinstance(row.payload, dict) else None
        try:
            row.organization_id = Payment.objects.using(alias).get(pk=payment_id).organization_id
        except (Payment.DoesNotExist, ValueError, TypeError):
            continue
        row.save(update_fields=["organization"])


class Migration(migrations.Migration):
    dependencies = [("payments", "0003_paymentattempt_needs_reconciliation_and_one_success"), ("invoices", "0003_invoice_organization")]
    operations = [
        migrations.AddField(model_name="paymentattempt", name="organization", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
        migrations.AddField(model_name="webhookevent", name="organization", field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
        migrations.AlterField(model_name="paymentattempt", name="idempotency_key", field=models.CharField(max_length=255, null=True, blank=True)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(model_name="paymentattempt", name="organization", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
        migrations.AddConstraint(model_name="paymentattempt", constraint=models.UniqueConstraint(fields=["organization", "idempotency_key"], name="unique_project_payment_key")),
    ]
