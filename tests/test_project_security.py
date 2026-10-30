from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import ApiKey, Customer, Organization, OrganizationMembership, User
from invoices.domain.models import Invoice, InvoiceLine, InvoiceStatus
from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt, WebhookEvent
from plans.models import Plan
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project(organization, user, customer, plan, subscription, invoice):
    OrganizationMembership.objects.create(organization=organization, user=user, role="owner")
    return organization, customer, plan, subscription, invoice


def client_for(user):
    client = APIClient()
    token, _ = Token.objects.get_or_create(user=user)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.mark.parametrize("method,path,data", [
    ("get", "/api/accounts/me/", None),
    ("get", "/api/accounts/organizations/", None),
    ("post", "/api/accounts/organizations/", {"name": "forged"}),
    ("get", "/api/accounts/invitations/", None),
    ("post", "/api/accounts/invitations/accept/", {"token": "invalid"}),
])
def test_api_key_cannot_act_as_a_human(project, method, path, data):
    _, key = ApiKey.create_for_organization(project[0])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
    assert getattr(client, method)(path, data, format="json").status_code == 403


@pytest.mark.parametrize("path,data", [
    ("/api/accounts/keys/", {"name": "escalation"}),
    ("/api/accounts/customers/", {"email": "new@example.com", "name": "New"}),
    ("/api/plans/", {"name": "New", "price": "1.00", "currency": "USD", "interval": "month"}),
    ("/api/subscriptions/", {"customer_id": 1, "plan_id": 1}),
    ("/api/payments/attempts/", {"invoice_id": 1}),
])
def test_member_cannot_mutate_project(project, path, data):
    member = User.objects.create_user(username="member@example.com", email="member@example.com", password="test-password")
    OrganizationMembership.objects.create(organization=project[0], user=member, role="member")
    assert client_for(member).post(path, data, format="json").status_code == 403


def test_member_cannot_revoke_key(project):
    key, _ = ApiKey.create_for_organization(project[0])
    member = User.objects.create_user(username="member@example.com", email="member@example.com", password="test-password")
    OrganizationMembership.objects.create(organization=project[0], user=member, role="member")
    assert client_for(member).delete(f"/api/accounts/keys/{key.pk}/").status_code == 403
    key.refresh_from_db()
    assert not key.revoked


def test_api_key_is_not_the_owner_principal(project):
    _, key = ApiKey.create_for_organization(project[0])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
    response = client.get("/api/plans/")
    assert response.status_code == 200
    principal = response.wsgi_request.user
    assert principal != project[0].owner
    assert principal.is_authenticated


@pytest.mark.parametrize("model", [Subscription, Invoice, InvoiceLine, PaymentAttempt, WebhookEvent])
def test_billing_records_have_structural_organization_scope(model):
    assert model._meta.get_field("organization").remote_field.model is Organization


def test_services_reject_customer_and_plan_from_different_projects(project):
    other = Organization.objects.create(owner=project[0].owner, name="Other")
    customer = Customer.objects.create(organization=other, email="other@example.com", name="Other")
    with pytest.raises((ValueError, ValidationError)):
        SubscriptionService.create(customer, project[2])


def issue_invoice(subscription):
    now = timezone.now()
    invoice = Invoice.objects.create(subscription=subscription, total="19.99", currency="USD",
                                     period_start=now, period_end=now + timedelta(days=30))
    invoice.transition_to(InvoiceStatus.ISSUED)
    return invoice


def test_idempotency_key_reuse_on_different_invoice_is_conflict(project):
    first = issue_invoice(project[3])
    second = issue_invoice(project[3])
    client = client_for(project[0].owner)
    headers = {"HTTP_IDEMPOTENCY_KEY": "same-request"}
    response = client.post("/api/payments/attempts/", {"invoice_id": first.pk}, format="json", **headers)
    assert response.status_code == 201
    replay = client.post("/api/payments/attempts/", {"invoice_id": first.pk}, format="json", **headers)
    assert replay.status_code == 201
    assert replay.data["id"] == response.data["id"]
    conflict = client.post("/api/payments/attempts/", {"invoice_id": second.pk}, format="json", **headers)
    assert conflict.status_code == 409
    assert not PaymentAttempt.objects.filter(invoice=second).exists()
