from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone


@pytest.fixture(autouse=True, scope="session")
def _isolate_from_env():
    # settings.py loads .env, and a developer's .env usually points the broker
    # and the throttle cache at docker hosts. Tests must run inline and
    # in-memory regardless, or they fail on a connection error instead of on
    # the thing under test. Done on the live settings: conftest is imported
    # after django.setup(), so environment variables are too late here.
    # Eager mode still acquires a producer on the broker, hence memory://;
    # broker_url itself reads the environment, so only the write url can be set.
    from config.celery import app as celery_app

    celery_app.conf.broker_write_url = "memory://"
    locmem = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    with override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
        CACHES=locmem,
        PAYMENT_FAILURE_RATE=0.0,
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    # Throttle counters live in the cache, which is shared
    # for the whole process. Clear it around each test so limits dont leak
    # across tests and flake the suite
    cache.clear()
    yield
    cache.clear()


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
