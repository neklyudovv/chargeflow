from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ApiKey, Customer, Organization
from invoices.domain.models import Invoice, InvoiceStatus
from invoices.tasks import run_dunning
from payments.application.gateway import ChargeResult
from payments.application.services import PaymentService, get_payment_gateway
from payments.domain.models import PaymentStatus
from payments.tasks import charge_invoice
from plans.models import Plan
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription, SubscriptionStatus

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def api(organization):
    _, secret = ApiKey.create_for_organization(organization, scope="read_write")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    return client


def test_late_renewal_preserves_month_end(customer, plan, monkeypatch):
    clock = [datetime(2028, 1, 31, 12, tzinfo=dt_timezone.utc)]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    monkeypatch.setattr(charge_invoice, "delay", lambda invoice_id: None)
    sub = SubscriptionService.create(customer, plan)
    for current, start, end in [
        (datetime(2028, 3, 2, 12, tzinfo=dt_timezone.utc), datetime(2028, 2, 29, 12, tzinfo=dt_timezone.utc), datetime(2028, 3, 31, 12, tzinfo=dt_timezone.utc)),
        (datetime(2028, 4, 1, 12, tzinfo=dt_timezone.utc), datetime(2028, 3, 31, 12, tzinfo=dt_timezone.utc), datetime(2028, 4, 30, 12, tzinfo=dt_timezone.utc)),
    ]:
        clock[0] = current
        SubscriptionService.renew(sub)
        sub.refresh_from_db()
        assert (sub.current_period_start, sub.current_period_end) == (start, end)
        assert sub.invoices.filter(period_start=start, period_end=end).count() == 1


def test_yearly_renewal_restores_leap_day(customer, plan, monkeypatch):
    clock = [datetime(2028, 2, 29, 12, tzinfo=dt_timezone.utc)]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    monkeypatch.setattr(charge_invoice, "delay", lambda invoice_id: None)
    plan.interval = "year"
    plan.save()
    sub = SubscriptionService.create(customer, plan)
    for year in range(2029, 2033):
        clock[0] = datetime(year, 3, 1, 12, tzinfo=dt_timezone.utc)
        SubscriptionService.renew(sub)
    sub.refresh_from_db()
    assert sub.current_period_start == datetime(2032, 2, 29, 12, tzinfo=dt_timezone.utc)
    assert sub.current_period_end == datetime(2033, 2, 28, 12, tzinfo=dt_timezone.utc)


def test_unknown_billing_interval_is_rejected(plan):
    plan.interval = "fortnight"
    with pytest.raises(ValueError):
        SubscriptionService._compute_period_end(timezone.now(), plan)


def test_retries_follow_first_failure_and_cancel_on_day_seven(customer, plan, monkeypatch):
    clock = [datetime(2028, 1, 1, 12, tzinfo=dt_timezone.utc)]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    monkeypatch.setattr(get_payment_gateway("mock"), "charge", lambda request: ChargeResult(False, request.idempotency_key, {}))
    sub = SubscriptionService.create(customer, plan)
    invoice = sub.invoices.get()
    first_failure = clock[0]
    for count, day in enumerate([1, 3, 7], start=1):
        invoice.refresh_from_db()
        sub.refresh_from_db()
        assert sub.status == SubscriptionStatus.OVERDUE
        assert invoice.next_retry_at == first_failure + timedelta(days=day)
        assert invoice.attempt_count == count
        clock[0] = first_failure + timedelta(days=day) - timedelta(seconds=1)
        run_dunning()
        assert invoice.payment_attempts.count() == count
        clock[0] += timedelta(seconds=1)
        run_dunning()
        run_dunning()
        assert invoice.payment_attempts.count() == count + 1
    sub.refresh_from_db()
    invoice.refresh_from_db()
    assert sub.status == SubscriptionStatus.CANCELED
    assert sub.canceled_reason == "non_payment"
    assert invoice.status == InvoiceStatus.OVERDUE
    assert invoice.next_retry_at is None
    clock[0] += timedelta(days=30)
    run_dunning()
    assert invoice.payment_attempts.count() == 4


def test_success_clears_retry_and_restores_access(customer, plan, monkeypatch):
    clock = [timezone.now()]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    monkeypatch.setattr(get_payment_gateway("mock"), "charge", lambda request: ChargeResult(False, request.idempotency_key, {}))
    sub = SubscriptionService.create(customer, plan)
    invoice = sub.invoices.get()
    declined = invoice.payment_attempts.get()
    PaymentService.settle(declined, False, {})
    invoice.refresh_from_db()
    assert invoice.attempt_count == 1
    assert invoice.next_retry_at == clock[0] + timedelta(days=1)
    PaymentService.settle(declined, True, {})
    sub.refresh_from_db()
    invoice.refresh_from_db()
    assert sub.status == SubscriptionStatus.ACTIVE
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.next_retry_at is None
    clock[0] += timedelta(days=2)
    run_dunning()
    assert invoice.payment_attempts.count() == 1


def test_dunning_skips_locked_subscription(customer, plan, monkeypatch):
    clock = [timezone.now()]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    monkeypatch.setattr(get_payment_gateway("mock"), "charge", lambda request: ChargeResult(False, request.idempotency_key, {}))
    sub = SubscriptionService.create(customer, plan)
    clock[0] += timedelta(days=1)

    def run():
        close_old_connections()
        try:
            run_dunning()
        finally:
            close_old_connections()

    with transaction.atomic(), ThreadPoolExecutor(max_workers=1) as pool:
        Subscription.objects.select_for_update().get(pk=sub.pk)
        pool.submit(run).result(timeout=5)
    assert sub.invoices.get().payment_attempts.count() == 1


def test_subscription_replay_and_conflicts(api, customer, plan, monkeypatch):
    monkeypatch.setattr(charge_invoice, "delay", lambda invoice_id: None)
    body = {"customer_id": customer.pk, "plan_id": plan.pk}
    first = api.post("/api/subscriptions/", body, format="json", HTTP_IDEMPOTENCY_KEY="subscribe-1")
    assert first.status_code == 201
    SubscriptionService.cancel(Subscription.objects.get(pk=first.data["id"]))
    replay = api.post("/api/subscriptions/", body, format="json", HTTP_IDEMPOTENCY_KEY="subscribe-1")
    assert replay.status_code == 200
    assert replay.data["id"] == first.data["id"]
    other = Customer.objects.create(organization=customer.organization, email="other@example.com")
    conflict = api.post("/api/subscriptions/", {**body, "customer_id": other.pk}, format="json", HTTP_IDEMPOTENCY_KEY="subscribe-1")
    assert conflict.status_code == 409
    assert Subscription.objects.count() == 1
    assert Invoice.objects.count() == 1


@pytest.mark.parametrize("status", ["trial", "active", "overdue"])
def test_live_subscription_duplicate_is_rejected(api, subscription, status):
    Subscription.objects.filter(pk=subscription.pk).update(status=status)
    response = api.post("/api/subscriptions/", {"customer_id": subscription.customer_id, "plan_id": subscription.plan_id}, format="json")
    assert response.status_code == 409
    assert Subscription.objects.count() == 1


def test_database_rejects_duplicate_live_subscription(subscription):
    with pytest.raises(IntegrityError), transaction.atomic():
        Subscription.objects.create(customer=subscription.customer, plan=subscription.plan, current_period_start=subscription.current_period_start, current_period_end=subscription.current_period_end)


def test_concurrent_subscription_replay_creates_one_invoice(customer, plan, monkeypatch):
    monkeypatch.setattr(charge_invoice, "delay", lambda invoice_id: None)
    barrier = Barrier(2)

    def create():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return SubscriptionService.create(customer, plan, idempotency_key="same-key").pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))
    assert ids[0] == ids[1]
    assert Subscription.objects.count() == Invoice.objects.count() == 1


@pytest.mark.parametrize("field,value", [("price", "-0.01"), ("currency", "zz"), ("currency", "ZZZ"), ("trial_period_days", -1)])
def test_plan_validation_rejects_invalid_api_values(api, plan, field, value):
    response = api.patch(f"/api/plans/{plan.pk}/", {field: value}, format="json")
    assert response.status_code == 400
    assert field in response.data


@pytest.mark.parametrize("field,value", [("price", "-0.01"), ("currency", "ZZZ"), ("trial_period_days", -1), ("interval", "fortnight")])
def test_plan_validation_rejects_invalid_model_values(plan, field, value):
    setattr(plan, field, value)
    with pytest.raises(ValidationError):
        plan.save()
