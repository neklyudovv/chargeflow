from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone


@pytest.fixture(autouse=True, scope="session")
def _isolate_from_env():
    # tests run inline and in-memory whatever the developer's .env says
    # (broker_url reads the environment directly, so only the write url can be swapped)
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
    # locmem counters live for the whole process and org pks repeat across rolled-back tests
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
    # defaults to TRIAL
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
    # defaults to DRAFT
    from invoices.domain.models import Invoice

    now = timezone.now()
    return Invoice.objects.create(
        subscription=subscription,
        total="19.99",
        currency="USD",
        period_start=now,
        period_end=now + timedelta(days=30),
    )
