import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import OrganizationMembership
from invoices.domain.models import Invoice

SUBSCRIPTIONS_URL = "/api/subscriptions/"


@pytest.fixture
def member(user, organization):
    # token-auth org resolution queries memberships, not Organization.owner.
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    return user


@pytest.fixture
def auth_client(member):
    token, _ = Token.objects.get_or_create(user=member)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def test_list_requires_authentication(db):
    response = APIClient().get(SUBSCRIPTIONS_URL)

    assert response.status_code == 401


def test_create_subscription_returns_201_active(auth_client, customer, plan):
    response = auth_client.post(
        SUBSCRIPTIONS_URL,
        {"customer_id": customer.pk, "plan_id": plan.pk},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["status"] == "active"  # plan fixture has no trial


def test_create_subscription_autogenerates_invoice(
    auth_client, customer, plan, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(
            SUBSCRIPTIONS_URL,
            {"customer_id": customer.pk, "plan_id": plan.pk},
            format="json",
        )

    assert response.status_code == 201
    assert Invoice.objects.filter(subscription_id=response.data["id"]).exists()


def test_created_subscription_appears_in_list(auth_client, customer, plan):
    auth_client.post(
        SUBSCRIPTIONS_URL,
        {"customer_id": customer.pk, "plan_id": plan.pk},
        format="json",
    )

    response = auth_client.get(SUBSCRIPTIONS_URL)

    assert response.status_code == 200
    assert len(response.data) == 1
