from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill(apps, schema_editor):
    Invoice = apps.get_model("invoices", "Invoice")
    Payment = apps.get_model("payments", "PaymentAttempt")
    alias = schema_editor.connection.alias
    for invoice in Invoice.objects.using(alias).iterator():
        payments = Payment.objects.using(alias).filter(invoice_id=invoice.pk)
        invoice.attempt_count = payments.count()
        failures = payments.filter(status="failed")
        if invoice.status == "failed":
            invoice.first_failed_at = failures.order_by("attempted_at").values_list("attempted_at", flat=True).first() or invoice.updated_at
            failed_count = failures.count()
            invoice.next_retry_at = (invoice.first_failed_at + timedelta(days=(1, 3, 7)[max(failed_count, 1) - 1]) if failed_count < 4 else timezone.now())
        invoice.save(update_fields=["attempt_count", "first_failed_at", "next_retry_at"])


class Migration(migrations.Migration):

    dependencies = [
        ('invoices', '0003_invoice_organization'),
        ('payments', '0005_payment_gateway'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='attempt_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='invoice',
            name='first_failed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='next_retry_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
