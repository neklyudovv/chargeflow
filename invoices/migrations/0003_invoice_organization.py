from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Invoice = apps.get_model("invoices", "Invoice")
    InvoiceLine = apps.get_model("invoices", "InvoiceLine")
    alias = schema_editor.connection.alias
    for row in Invoice.objects.using(alias).select_related("subscription").iterator():
        row.organization_id = row.subscription.organization_id
        row.save(update_fields=["organization"])
    for row in InvoiceLine.objects.using(alias).select_related("invoice").iterator():
        row.organization_id = row.invoice.organization_id
        row.save(update_fields=["organization"])


class Migration(migrations.Migration):
    dependencies = [("invoices", "0002_invoice_unique_invoice_per_subscription_period"), ("subscriptions", "0003_subscription_organization")]
    operations = [
        migrations.AddField(model_name=name, name="organization", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="accounts.organization"))
        for name in ["invoice", "invoiceline"]
    ] + [migrations.RunPython(backfill, migrations.RunPython.noop)] + [
        migrations.AlterField(model_name=name, name="organization", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.organization"))
        for name in ["invoice", "invoiceline"]
    ]
