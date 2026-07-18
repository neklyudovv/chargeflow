from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import OrganizationMembership
from invoices.domain.models import Invoice
from subscriptions.domain.models import SubscriptionStatus

SUBSCRIPTIONS_URL = "/api/subscriptions/"


def _set(subscription, status, period_end):
    subscription.status = status
    subscription.current_period_end = period_end
    subscription.save(update_fields=["status", "current_period_end"])


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


def test_cancel_endpoint_exposes_reason(auth_client, customer, plan):
    created = auth_client.post(
        SUBSCRIPTIONS_URL,
        {"customer_id": customer.pk, "plan_id": plan.pk},
        format="json",
    )
    subscription_id = created.data["id"]

    response = auth_client.post(f"{SUBSCRIPTIONS_URL}{subscription_id}/cancel/")

    assert response.status_code == 200
    assert response.data["status"] == "canceled"
    assert response.data["canceled_reason"] == "voluntary"


def test_renew_endpoint_rejects_a_subscription_mid_period(auth_client, subscription):
    # two clicks must not bill the customer for a period they already have
    _set(subscription, SubscriptionStatus.ACTIVE, timezone.now() + timedelta(days=10))

    response = auth_client.post(f"{SUBSCRIPTIONS_URL}{subscription.pk}/renew/")

    assert response.status_code == 400
    assert not Invoice.objects.filter(subscription=subscription).exists()


def test_renew_endpoint_renews_an_expired_subscription(auth_client, subscription):
    _set(subscription, SubscriptionStatus.ACTIVE, timezone.now() - timedelta(hours=1))

    response = auth_client.post(f"{SUBSCRIPTIONS_URL}{subscription.pk}/renew/")

    assert response.status_code == 200
    subscription.refresh_from_db()
    assert subscription.current_period_end > timezone.now()


def test_activate_endpoint_rejects_an_overdue_subscription(auth_client, subscription):
    # activating resets the billing period - on an OVERDUE subscription that
    # would clear the arrears without anyone paying them
    period_end = timezone.now() - timedelta(hours=1)
    _set(subscription, SubscriptionStatus.OVERDUE, period_end)

    response = auth_client.post(f"{SUBSCRIPTIONS_URL}{subscription.pk}/activate/")

    assert response.status_code == 400
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.OVERDUE
    assert subscription.current_period_end == period_end


def test_activate_endpoint_ends_a_trial(auth_client, subscription):
    response = auth_client.post(f"{SUBSCRIPTIONS_URL}{subscription.pk}/activate/")

    assert response.status_code == 200
    assert response.data["status"] == "active"


def test_create_on_archived_plan_returns_400(auth_client, customer, plan):
    from plans.models import PlanStatus

    plan.status = PlanStatus.ARCHIVED
    plan.save(update_fields=["status"])

    response = auth_client.post(
        SUBSCRIPTIONS_URL,
        {"customer_id": customer.pk, "plan_id": plan.pk},
        format="json",
    )

    assert response.status_code == 400
    assert "archived" in str(response.data).lower()


def test_delete_plan_with_subscriptions_returns_400(auth_client, plan, subscription):
    # subscription fixture already references plan; PROTECT must surface as 400
    response = auth_client.delete(f"/api/plans/{plan.pk}/")

    assert response.status_code == 400
    from plans.models import Plan

    assert Plan.objects.filter(pk=plan.pk).exists()


def test_created_subscription_appears_in_list(auth_client, customer, plan):
    auth_client.post(
        SUBSCRIPTIONS_URL,
        {"customer_id": customer.pk, "plan_id": plan.pk},
        format="json",
    )

    response = auth_client.get(SUBSCRIPTIONS_URL)

    assert response.status_code == 200
    assert len(response.data) == 1
