from django.db import migrations, models
from django.db.models.functions import ExtractDay


def prepare_existing(apps, schema_editor):
    Subscription = apps.get_model("subscriptions", "Subscription")
    rows = Subscription.objects.using(schema_editor.connection.alias)
    duplicates = rows.filter(status__in=["trial", "active", "overdue"]).values("customer_id", "plan_id").annotate(count=models.Count("id")).filter(count__gt=1)
    if duplicates.exists():
        raise ValueError("Resolve duplicate live customer/plan subscriptions before migrating; no subscriptions were canceled automatically.")
    rows.update(billing_anchor_day=ExtractDay("current_period_start"))


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_customer_provider_tokens'),
        ('plans', '0002_alter_plan_currency_alter_plan_price_and_more'),
        ('subscriptions', '0003_subscription_organization'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='billing_anchor_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.RunPython(prepare_existing, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='subscription',
            constraint=models.UniqueConstraint(condition=models.Q(('status__in', ['trial', 'active', 'overdue'])), fields=('customer', 'plan'), name='one_live_subscription_per_customer_plan'),
        ),
        migrations.AddConstraint(
            model_name='subscription',
            constraint=models.UniqueConstraint(fields=('organization', 'idempotency_key'), name='unique_project_subscription_key'),
        ),
    ]
