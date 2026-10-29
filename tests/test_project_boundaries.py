from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ApiKey, Customer, Organization, OrganizationMembership, User
from payments.application.services import IdempotencyConflict, PaymentService
from payments.domain.models import PaymentAttempt, WebhookEvent
from subscriptions.application.services import SubscriptionService
from tests.test_project_security import client_for, issue_invoice

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_project_admin_can_create_scoped_key(organization, user, role):
    OrganizationMembership.objects.create(organization=organization, user=user, role=role)
    client = client_for(user)
    response = client.post("/api/accounts/keys/", {"name": "Read only", "scope": "read"}, format="json")
    assert response.status_code == 201
    assert response.data["scope"] == "read"
    assert client.delete(f"/api/accounts/keys/{response.data['id']}/").status_code == 204


@pytest.mark.parametrize("scope,expected", [("read", 403), ("read_write", 201)])
def test_api_key_scope_controls_project_mutations(organization, scope, expected):
    key, secret = ApiKey.create_for_organization(organization, scope=scope)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert client.get("/api/accounts/customers/").status_code == 200
    response = client.post("/api/accounts/customers/", {"name": "New", "email": "new@example.com"}, format="json")
    assert response.status_code == expected
    assert client.post("/api/accounts/keys/", {"scope": "read_write"}, format="json").status_code == 403
    assert client.get("/api/accounts/members/").status_code == 403
    key.revoked = True
    key.save(update_fields=["revoked"])
    assert client.get("/api/plans/").status_code in [401, 403]


def test_expired_key_is_rejected(organization):
    _, secret = ApiKey.create_for_organization(organization, expires_at=timezone.now() - timedelta(seconds=1))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert client.get("/api/plans/").status_code in [401, 403]


@pytest.mark.parametrize("method,path", [
    ("patch", "/api/accounts/customers/{customer}/"),
    ("patch", "/api/plans/{plan}/"),
    ("delete", "/api/plans/{plan}/"),
    ("post", "/api/subscriptions/{subscription}/activate/"),
    ("post", "/api/subscriptions/{subscription}/cancel/"),
    ("post", "/api/subscriptions/{subscription}/renew/"),
])
def test_member_and_read_key_cannot_change_existing_records(organization, user, customer, plan, subscription, method, path):
    OrganizationMembership.objects.create(organization=organization, user=user, role="member")
    path = path.format(customer=customer.pk, plan=plan.pk, subscription=subscription.pk)
    _, secret = ApiKey.create_for_organization(organization, scope="read")
    key_client = APIClient()
    key_client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    for client in [client_for(user), key_client]:
        assert getattr(client, method)(path, {}, format="json").status_code == 403
    customer.refresh_from_db()
    subscription.refresh_from_db()
    assert subscription.status == "trial"
    assert client_for(user).get("/api/subscriptions/").status_code == 200


def test_structural_scope_follows_billing_chain(organization, customer, plan, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        subscription = SubscriptionService.create(customer, plan)
    invoice = subscription.invoices.get()
    payment = invoice.payment_attempts.get()
    line = invoice.lines.get()
    PaymentService.handle_webhook("payment.success", {"payment_attempt_id": payment.pk, "status": "success"})
    assert {row.organization_id for row in [subscription, invoice, line, payment, WebhookEvent.objects.get()]} == {organization.pk}


def test_cross_project_parent_assignment_and_moves_are_rejected(organization, customer, plan, subscription, invoice):
    other = Organization.objects.create(owner=organization.owner, name="Other")
    invoice.organization = other
    with pytest.raises(ValidationError):
        invoice.save()
    customer.organization = other
    with pytest.raises(ValidationError):
        customer.save()
    plan.organization = other
    with pytest.raises(ValidationError):
        plan.save()


def test_payment_keys_are_independent_between_projects(organization, user, subscription):
    from plans.models import Plan
    other = Organization.objects.create(owner=user, name="Other")
    other_customer = Customer.objects.create(organization=other, name="Other", email="other@example.com")
    other_plan = Plan.objects.create(organization=other, name="Other", price="19.99", trial_period_days=1)
    other_subscription = SubscriptionService.create(other_customer, other_plan)
    first = PaymentService.attempt(issue_invoice(subscription), "same-key")
    second = PaymentService.attempt(issue_invoice(other_subscription), "same-key")
    assert first.pk != second.pk
    assert (first.organization_id, second.organization_id) == (organization.pk, other.pk)


@pytest.mark.django_db(transaction=True)
def test_concurrent_key_reuse_on_distinct_invoices_conflicts(organization, customer, plan):
    plan.trial_period_days = 1
    plan.save()
    subscriptions = [SubscriptionService.create(customer, plan) for _ in range(2)]
    invoices = [issue_invoice(subscription) for subscription in subscriptions]
    barrier = Barrier(2)

    def charge(invoice):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                PaymentService.attempt(invoice, "racing-key")
                return "charged"
            except IdempotencyConflict:
                return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(charge, invoices)) == ["charged", "conflict"]
    assert PaymentAttempt.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mismatched", [False, True])
def test_organization_migration_preserves_existing_billing_rows(mismatched):
    previous = [("accounts", "0004_unique_org_owner_constraint"), ("subscriptions", "0002_subscription_canceled_reason"), ("invoices", "0002_invoice_unique_invoice_per_subscription_period"), ("payments", "0003_paymentattempt_needs_reconciliation_and_one_success")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(previous)
    try:
        apps = executor.loader.project_state(previous).apps
        user = apps.get_model("accounts", "User").objects.create(username="historic", email="historic@example.com")
        org = apps.get_model("accounts", "Organization").objects.create(owner=user, name="Historic")
        customer = apps.get_model("accounts", "Customer").objects.create(organization=org, email="historic@example.com", name="Historic")
        plan = apps.get_model("plans", "Plan").objects.create(organization=org, name="Historic", price="1.00")
        now = timezone.now()
        subscription = apps.get_model("subscriptions", "Subscription").objects.create(customer=customer, plan=plan, current_period_start=now, current_period_end=now + timedelta(days=1))
        invoice = apps.get_model("invoices", "Invoice").objects.create(subscription=subscription, total="1.00", currency="USD", period_start=now, period_end=now + timedelta(days=1))
        line = apps.get_model("invoices", "InvoiceLine").objects.create(invoice=invoice, description="Historic", amount="1.00")
        payment = apps.get_model("payments", "PaymentAttempt").objects.create(invoice=invoice, amount="1.00", idempotency_key="historic-key")
        webhook_model = apps.get_model("payments", "WebhookEvent")
        known = webhook_model.objects.create(event_type="known", payload={"payment_attempt_id": payment.pk})
        unknown = webhook_model.objects.create(event_type="unknown", payload={})
        if mismatched:
            other = apps.get_model("accounts", "Organization").objects.create(owner=user, name="Wrong project")
            plan.organization = other
            plan.save(update_fields=["organization"])
            with pytest.raises(ValueError, match="mismatched customer and plan"):
                MigrationExecutor(connection).migrate(latest)
            plan.organization = org
            plan.save(update_fields=["organization"])
        MigrationExecutor(connection).migrate(latest)
        from invoices.domain.models import Invoice, InvoiceLine
        from subscriptions.domain.models import Subscription
        for model, pk in [(Subscription, subscription.pk), (Invoice, invoice.pk), (InvoiceLine, line.pk), (PaymentAttempt, payment.pk), (WebhookEvent, known.pk)]:
            assert model.objects.get(pk=pk).organization_id == org.pk
        assert WebhookEvent.objects.get(pk=unknown.pk).organization_id is None
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.parametrize("scope", ["invalid", "owner"])
def test_invalid_key_scope_is_rejected(organization, user, scope):
    OrganizationMembership.objects.create(organization=organization, user=user, role="owner")
    assert client_for(user).post("/api/accounts/keys/", {"scope": scope}, format="json").status_code == 400


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_admin_can_mutate_project_data(organization, user, customer, plan, subscription, role):
    OrganizationMembership.objects.create(organization=organization, user=user, role=role)
    client = client_for(user)
    assert client.patch(f"/api/accounts/customers/{customer.pk}/", {"name": "Updated"}, format="json").status_code == 200
    assert client.patch(f"/api/plans/{plan.pk}/", {"name": "Updated"}, format="json").status_code == 200
    assert client.post(f"/api/subscriptions/{subscription.pk}/cancel/", {}, format="json").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_key_serialization_does_not_deadlock_new_billing_foreign_keys(organization, customer, plan):
    from django.db import transaction
    plan.trial_period_days = 1
    plan.save()
    barrier = Barrier(2)

    def create_and_charge(number):
        close_old_connections()
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '5s'")
                subscription = SubscriptionService.create(customer, plan)
                invoice = issue_invoice(subscription)
                barrier.wait(timeout=10)
                PaymentService.attempt(invoice, f"new-key-{number}")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(create_and_charge, [1, 2]))
    assert PaymentAttempt.objects.count() == 2
