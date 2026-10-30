from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections
from django.utils import timezone

from invoices.domain.models import PAYABLE_INVOICE_STATUSES, Invoice, InvoiceStatus
from payments.application.services import (
    InvoiceNotPayable,
    PaymentService,
    get_payment_gateway,
)
from payments.application.gateway import ChargeResult
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.domain.models import Subscription, SubscriptionStatus

NON_PAYABLE_STATUSES = sorted(set(InvoiceStatus.values) - PAYABLE_INVOICE_STATUSES)
pytestmark = pytest.mark.django_db(transaction=True)


def _force_status(invoice, status):
    invoice.status = status
    invoice.save(update_fields=["status"])


def test_attempt_charges_payable_invoice(invoice):
    _force_status(invoice, InvoiceStatus.ISSUED)

    payment = PaymentService.attempt(invoice)

    invoice.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
    assert payment.amount == invoice.total
    assert payment.provider_response == {"status": "ok"}


@pytest.mark.parametrize("status", NON_PAYABLE_STATUSES)
def test_attempt_rejects_non_payable_invoice(invoice, status):
    _force_status(invoice, status)

    with pytest.raises(InvoiceNotPayable):
        PaymentService.attempt(invoice)

    assert not PaymentAttempt.objects.filter(invoice=invoice).exists()


def test_attempt_records_decline_when_provider_fails(invoice, monkeypatch):
    monkeypatch.setattr(
        get_payment_gateway("mock"),
        "charge",
        lambda request: ChargeResult(False, "charge_declined", {"status": "declined"}),
    )
    _force_status(invoice, InvoiceStatus.ISSUED)

    payment = PaymentService.attempt(invoice)

    assert payment.status == PaymentStatus.FAILED
    assert payment.provider_response == {"status": "declined"}


def test_attempt_replays_by_idempotency_key(invoice, monkeypatch):
    # a decline leaves the invoice payable, so only the key keeps the retry from charging again
    monkeypatch.setattr(
        get_payment_gateway("mock"),
        "charge",
        lambda request: ChargeResult(False, "charge_declined", {"status": "declined"}),
    )
    _force_status(invoice, InvoiceStatus.ISSUED)

    first = PaymentService.attempt(invoice, idempotency_key="req-123")
    assert first.status == PaymentStatus.FAILED

    second = PaymentService.attempt(invoice, idempotency_key="req-123")

    assert second.pk == first.pk
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1


def test_concurrent_provider_charge_id_is_applied_to_only_one_invoice(
    customer, plan, subscription, invoice
):
    now = timezone.now()
    Subscription.objects.filter(pk=subscription.pk).update(
        status=SubscriptionStatus.ACTIVE
    )
    Invoice.objects.filter(pk=invoice.pk).update(status=InvoiceStatus.ISSUED)
    second_subscription = Subscription.objects.create(
        customer=customer,
        plan=plan,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now + timedelta(days=31),
        current_period_end=now + timedelta(days=61),
    )
    second_invoice = Invoice.objects.create(
        subscription=second_subscription,
        status=InvoiceStatus.ISSUED,
        total=invoice.total,
        currency=invoice.currency,
        period_start=now + timedelta(days=31),
        period_end=now + timedelta(days=61),
    )
    attempts = [
        PaymentAttempt.objects.create(invoice=current, amount=current.total)
        for current in (invoice, second_invoice)
    ]
    barrier = Barrier(2)

    def settle(attempt_id):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            PaymentService.settle(
                PaymentAttempt.objects.get(pk=attempt_id),
                True,
                {"provider_charge_id": "charge-shared"},
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(settle, [attempt.pk for attempt in attempts]))

    assert PaymentAttempt.objects.filter(status=PaymentStatus.SUCCESS).count() == 1
    assert PaymentAttempt.objects.filter(needs_reconciliation=True).count() == 1
    assert Invoice.objects.filter(status=InvoiceStatus.PAID).count() == 1
