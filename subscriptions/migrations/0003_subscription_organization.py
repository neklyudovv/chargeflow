from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Subscription = apps.get_model("subscriptions", "Subscription")
    for row in Subscription.objects.using(schema_editor.connection.alias).select_related("customer", "plan").iterator():
        if row.customer.organization_id != row.plan.organization_id:
            raise ValueError("Cannot assign organization to a subscription with mismatched customer and plan.")
        row.organization_id = row.customer.organization_id
        row.save(update_fields=["organization"])


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0002_subscription_canceled_reason")]
    operations = [
        migrations.AddField(model_name="subscription", name="organization", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(model_name="subscription", name="organization", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
    ]
