from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction

from accounts.models import Customer, Organization
from plans.models import Plan
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("parent_type", ["customer", "plan"])
def test_parent_move_waits_for_subscription_creation(organization, customer, plan, parent_type):
    plan.trial_period_days = 1
    plan.save()
    other = Organization.objects.create(owner=organization.owner, name="Other")
    parent = Customer.objects.get(pk=customer.pk) if parent_type == "customer" else Plan.objects.get(pk=plan.pk)
    parent.organization = other
    created, release, moving = Event(), Event(), Event()
    mover_pid = []

    def create():
        close_old_connections()
        try:
            with transaction.atomic():
                SubscriptionService.create(customer, plan)
                created.set()
                assert release.wait(timeout=10)
        finally:
            close_old_connections()

    def move():
        close_old_connections()
        try:
            assert created.wait(timeout=10)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                mover_pid.append(cursor.fetchone()[0])
            moving.set()
            with pytest.raises(ValidationError):
                parent.save()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        creator, mover = pool.submit(create), pool.submit(move)
        try:
            assert moving.wait(timeout=10)
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline and not mover.done():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [mover_pid[0]])
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                sleep(0.01)
            assert blocked
        finally:
            release.set()
        creator.result(timeout=10)
        mover.result(timeout=10)
    subscription = Subscription.objects.select_related("customer", "plan").get()
    assert subscription.organization_id == subscription.customer.organization_id == subscription.plan.organization_id


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["invoice", "line", "payment"])
def test_descendant_scope_ignores_unsaved_parent_scope(organization, subscription, invoice, kind):
    from django.utils import timezone
    from invoices.domain.models import Invoice, InvoiceLine
    from payments.domain.models import PaymentAttempt

    other = Organization.objects.create(owner=organization.owner, name="Other")
    subscription.organization = other
    invoice.organization = other
    if kind == "invoice":
        now = timezone.now()
        row = Invoice.objects.create(subscription=subscription, total="1.00", currency="USD", period_start=now, period_end=now)
    elif kind == "line":
        row = InvoiceLine.objects.create(invoice=invoice, description="Line", amount="1.00")
    else:
        row = PaymentAttempt.objects.create(invoice=invoice, amount="1.00")
    row.refresh_from_db()
    assert row.organization_id == organization.pk
