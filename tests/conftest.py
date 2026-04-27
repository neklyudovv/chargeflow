from datetime import timedelta

import pytest
from django.utils import timezone


@pytest.fixture
def user(db):
    from accounts.models import User

    u = User(email="owner@example.com")
    u.set_password("pw-owner-123!")
    u.save()
    return u


@pytest.fixture
def organization(db, user):
    from accounts.models import Organization

    return Organization.objects.create(owner=user, name="Acme Inc")


@pytest.fixture
def plan(db, organization):
    from plans.models import Plan

    return Plan.objects.create(
        organization=organization,
        name="Pro",
        price="19.99",
        currency="USD",
    )


@pytest.fixture
def customer(db, organization):
    from accounts.models import Customer

    return Customer.objects.create(
        organization=organization,
        email="customer@example.com",
        name="Casey Customer",
    )


@pytest.fixture
def subscription(db, customer, plan):
    """a fresh subscription. defaults to TRIAL status"""
    from subscriptions.domain.models import Subscription

    now = timezone.now()
    return Subscription.objects.create(
        customer=customer,
        plan=plan,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )


@pytest.fixture
def invoice(db, subscription):
    """a fresh invoice for the subscription, defaults to DRAFT status"""
    from invoices.domain.models import Invoice

    now = timezone.now()
    return Invoice.objects.create(
        subscription=subscription,
        total="19.99",
        currency="USD",
        period_start=now,
        period_end=now + timedelta(days=30),
    )
