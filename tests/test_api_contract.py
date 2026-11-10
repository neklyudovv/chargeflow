import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from rest_framework.test import APIClient

from accounts.models import ApiKey, Customer, Organization, OrganizationMembership
from payments.domain.models import PaymentAttempt
from subscriptions.domain.models import Subscription

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def human(user, organization):
    OrganizationMembership.objects.create(organization=organization, user=user, role="owner")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def service(organization):
    _, secret = ApiKey.create_for_organization(organization, scope="read_write")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    return client


def validate_response(schema, path, method, response):
    body = schema["paths"][path][method]["responses"][str(response.status_code)]["content"]["application/json"]["schema"]
    Draft202012Validator({**body, "components": schema["components"]}, format_checker=FormatChecker()).validate(response.json())


def test_user_and_service_credentials_work_until_revoked():
    human = APIClient()
    credentials = {"email": "new@example.com", "password": "test-strong-password-257!"}
    registered = human.post("/api/v1/auth/register/", credentials, format="json")
    assert registered.status_code == 201
    human.credentials(HTTP_AUTHORIZATION=f"Token {registered.data['token']}")
    account = human.get("/api/v1/me/")
    assert account.status_code == 200
    assert account.data["user"]["id"] == registered.data["user"]["id"]

    login = APIClient().post("/api/v1/auth/login/", credentials, format="json")
    assert login.status_code == 200
    human.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")
    created = human.post("/api/v1/organizations/", {"name": "Example"}, format="json")
    assert created.status_code == 201
    assert created.data["organization"]["my_role"] == "owner"
    key = created.data["api_key"]
    service = APIClient()
    service.credentials(HTTP_AUTHORIZATION=f"Bearer {key['key']}")
    assert service.get("/api/v1/plans/").status_code == 200

    listed = human.get("/api/v1/api-keys/")
    assert listed.status_code == 200
    assert key["id"] in {item["id"] for item in listed.data["results"]}
    assert all("key" not in item for item in listed.data["results"])
    assert human.delete(f"/api/v1/api-keys/{key['id']}/").status_code == 204
    assert service.get("/api/v1/plans/").status_code == 401


def test_ambiguous_project_requires_explicit_selection(human, organization, user):
    other = Organization.objects.create(owner=user, name="Other")
    OrganizationMembership.objects.create(organization=other, user=user, role="owner")
    response = human.get("/api/v1/me/")
    assert response.status_code == 400
    assert response.data["code"] == "organization_ambiguous"
    selected = human.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=str(organization.pk))
    assert selected.status_code == 200
    assert selected.data["organization"]["id"] == organization.pk


def test_pagination_preserves_existing_customers_during_insert(service, organization, customer):
    now = timezone.now()
    expected = {customer.pk}
    for number in range(3):
        row = Customer.objects.create(organization=organization, email=f"page-{number}@example.com")
        Customer.objects.filter(pk=row.pk).update(created_at=now - timedelta(seconds=number + 1))
        expected.add(row.pk)
    response = service.get("/api/v1/customers/", {"page_size": 2})
    assert response.status_code == 200
    assert len(response.data["results"]) == 2
    assert response.data["next"]
    seen = [row["id"] for row in response.data["results"]]
    Customer.objects.create(organization=organization, email="inserted@example.com")
    for _ in range(len(expected)):
        if not response.data["next"]:
            break
        parts = urlsplit(response.data["next"])
        response = service.get(parts.path + "?" + parts.query)
        assert response.status_code == 200
        seen.extend(row["id"] for row in response.data["results"])
    assert not response.data["next"]
    assert set(seen) == expected
    assert len(seen) == len(expected)

    invalid = service.get("/api/v1/customers/", {"cursor": "!!!"})
    assert invalid.status_code == 400
    assert "cursor" in invalid.data["fields"]


def test_create_rejects_foreign_references_and_unknown_fields(service, organization, plan):
    other = Organization.objects.create(owner=organization.owner, name="Other")
    foreign = Customer.objects.create(organization=other, email="foreign@example.com")
    response = service.post("/api/v1/subscriptions/", {"customer_id": foreign.pk, "plan_id": plan.pk}, format="json")
    assert response.status_code == 400
    assert "customer_id" in response.data["fields"]
    assert not Subscription.objects.exists()

    before = Customer.objects.count()
    unknown = service.post("/api/v1/customers/", {"email": "new@example.com", "organization_id": other.pk}, format="json")
    assert unknown.status_code == 400
    assert "organization_id" in unknown.data["fields"]
    assert Customer.objects.count() == before


def test_billing_http_matches_the_published_contract(service, customer):
    schema = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text())
    request_schema = schema["paths"]["/api/v1/plans/"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    validator = Draft202012Validator({**request_schema, "components": schema["components"]})
    for price in ["-0.01", "19.99"]:
        body = {"name": "Contract plan", "price": price}
        plan = service.post("/api/v1/plans/", body, format="json")
        assert validator.is_valid(body) == (plan.status_code == 201)
        validate_response(schema, "/api/v1/plans/", "post", plan)
    assert plan.status_code == 201
    created = service.post("/api/v1/subscriptions/", {"customer_id": customer.pk, "plan_id": plan.data["id"]}, format="json")
    assert created.status_code == 201
    validate_response(schema, "/api/v1/subscriptions/", "post", created)
    invoice = Subscription.objects.get(pk=created.data["id"]).invoices.get()
    detail = service.get(f"/api/v1/invoices/{invoice.pk}/")
    assert detail.status_code == 200
    validate_response(schema, "/api/v1/invoices/{id}/", "get", detail)
    attempt = service.get(f"/api/v1/payment-attempts/{invoice.payment_attempts.get().pk}/")
    assert attempt.status_code == 200
    validate_response(schema, "/api/v1/payment-attempts/{id}/", "get", attempt)


def test_failed_creation_hides_internal_error_details(human, monkeypatch):
    from accounts.api.serializers import CreateOrganizationRequest

    private_detail = "sensitive-upstream-details"

    def fail(*args, **kwargs):
        raise RuntimeError(private_detail)

    monkeypatch.setattr(CreateOrganizationRequest, "save", fail)
    response = human.post("/api/v1/organizations/", {"name": "Example"}, format="json")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert private_detail.encode() not in response.content


def test_payment_list_preserves_unfiltered_reconciliation_records(service, invoice):
    normal = PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total)
    flagged = PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total, needs_reconciliation=True)
    all_items = service.get("/api/v1/payment-attempts/")
    assert all_items.status_code == 200
    assert {row["id"] for row in all_items.data["results"]} == {normal.pk, flagged.pk}
    for value, expected in [("true", flagged.pk), ("false", normal.pk)]:
        response = service.get("/api/v1/payment-attempts/", {"needs_reconciliation": value})
        assert response.status_code == 200
        assert [row["id"] for row in response.data["results"]] == [expected]


def test_api_modules_start_without_import_order_dependency():
    code = "import django; django.setup(); import accounts.request_context; import payments.api.serializers; import subscriptions.api.serializers"
    result = subprocess.run(
        [sys.executable, "-c", code], env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_lifecycle_actions_reject_unexpected_input(service, subscription):
    response = service.post(f"/api/v1/subscriptions/{subscription.pk}/activate/", {"reason": "non_payment"}, format="json")
    assert response.status_code == 400
    subscription.refresh_from_db()
    assert subscription.status == "trial"
    assert not subscription.invoices.exists()
