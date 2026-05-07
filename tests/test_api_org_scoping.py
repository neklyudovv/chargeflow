from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import (
    ApiKey,
    Customer,
    Organization,
    OrganizationMembership,
    User,
)
from plans.models import Plan
from subscriptions.domain.models import Subscription

SUBSCRIPTIONS_URL = "/api/subscriptions/"


def _build_org(email, name):
    user = User(email=email)
    user.set_password("pw-123456!")
    user.save()
    org = Organization.objects.create(owner=user, name=name)
    OrganizationMembership.objects.create(
        organization=org,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    plan = Plan.objects.create(
        organization=org, name="Pro", price="19.99", currency="USD"
    )
    customer = Customer.objects.create(
        organization=org, email="customer@example.com", name="Cust"
    )
    now = timezone.now()
    subscription = Subscription.objects.create(
        customer=customer,
        plan=plan,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    _, raw_key = ApiKey.create_for_organization(org, name="Default")
    token, _ = Token.objects.get_or_create(user=user)
    return {
        "org": org,
        "subscription": subscription,
        "raw_key": raw_key,
        "token": token.key,
    }


@pytest.fixture
def org_a(db):
    return _build_org("a@example.com", "Org A")


@pytest.fixture
def org_b(db):
    return _build_org("b@example.com", "Org B")


def test_api_key_auth_resolves_organization(org_a):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {org_a['raw_key']}")

    response = client.get(SUBSCRIPTIONS_URL)

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [org_a["subscription"].pk]


def test_invalid_api_key_is_rejected(db):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer cf_not_a_real_key")

    response = client.get(SUBSCRIPTIONS_URL)

    assert response.status_code == 401


def test_org_cannot_see_another_orgs_subscriptions(org_a, org_b):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {org_a['raw_key']}")

    response = client.get(SUBSCRIPTIONS_URL)

    ids = {row["id"] for row in response.data}
    assert ids == {org_a["subscription"].pk}
    assert org_b["subscription"].pk not in ids


def test_unresolved_org_returns_empty_not_forbidden(db):
    # Authenticated, but no membership -> org can't be resolved.
    user = User(email="loner@example.com")
    user.set_password("pw-123456!")
    user.save()
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    response = client.get(SUBSCRIPTIONS_URL)

    assert response.status_code == 200
    assert response.data == []
