from datetime import datetime, timedelta, timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("duplicate", [False, True])
def test_existing_billing_data_is_backfilled_or_rejected_safely(duplicate):
    previous = [("subscriptions", "0003_subscription_organization"), ("invoices", "0003_invoice_organization"), ("payments", "0005_payment_gateway"), ("plans", "0001_initial")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(previous)
    duplicate_pk = None
    try:
        apps = executor.loader.project_state(previous).apps
        user = apps.get_model("accounts", "User").objects.create(username="historic-billing", email="historic-billing@example.com")
        org = apps.get_model("accounts", "Organization").objects.create(owner=user, name="Historic")
        customer = apps.get_model("accounts", "Customer").objects.create(organization=org, email="historic@example.com")
        plan = apps.get_model("plans", "Plan").objects.create(organization=org, name="Historic", price="1.00")
        start = datetime(2028, 1, 31, 12, tzinfo=timezone.utc)
        subscriptions = apps.get_model("subscriptions", "Subscription").objects
        values = dict(organization=org, customer=customer, plan=plan, status="overdue", current_period_start=start, current_period_end=start + timedelta(days=29))
        sub = subscriptions.create(**values)
        invoice = apps.get_model("invoices", "Invoice").objects.create(organization=org, subscription=sub, status="failed", total="1.00", currency="USD", period_start=start, period_end=sub.current_period_end)
        payment = apps.get_model("payments", "PaymentAttempt").objects.create(organization=org, invoice=invoice, amount="1.00", status="failed", provider_idempotency_key="historic-billing")
        apps.get_model("payments", "PaymentAttempt").objects.filter(pk=payment.pk).update(attempted_at=start)
        if duplicate:
            duplicate_pk = subscriptions.create(**values).pk
            with pytest.raises(ValueError, match="duplicate live"):
                MigrationExecutor(connection).migrate(latest)
            subscriptions.filter(pk=duplicate_pk).delete()
            duplicate_pk = None
        MigrationExecutor(connection).migrate(latest)
        from invoices.domain.models import Invoice
        from subscriptions.domain.models import Subscription
        migrated = Invoice.objects.get(pk=invoice.pk)
        assert migrated.attempt_count == 1
        assert migrated.first_failed_at == start
        assert migrated.next_retry_at == start + timedelta(days=1)
        assert Subscription.objects.get(pk=sub.pk).billing_anchor_day == 31
    finally:
        if duplicate_pk:
            subscriptions.filter(pk=duplicate_pk).delete()
        MigrationExecutor(connection).migrate(latest)
