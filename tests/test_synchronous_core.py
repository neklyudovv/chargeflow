"""locked tests of the synchronous core (REVIEW.md D2/D3): state changes happen in one
transaction through direct service calls; the only deferred work is handing a charge
to the worker, and a lost hand-off is recoverable
"""
import importlib.util
import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from invoices.domain.models import Invoice, InvoiceStatus
from invoices.tasks import MAX_DUNNING_ATTEMPTS, _dun_invoice, run_dunning
from payments.application.services import MockPaymentProvider, PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import CancellationReason, SubscriptionStatus


def _force(obj, status):
    obj.status = status
    obj.save(update_fields=["status"])


def _decline(monkeypatch):
    monkeypatch.setattr(MockPaymentProvider, "charge", staticmethod(lambda a, c: False))


def test_create_issues_the_first_invoice_in_the_same_transaction(
    customer, plan, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        subscription = SubscriptionService.create(customer, plan)

    invoice = Invoice.objects.get(subscription=subscription)
    assert invoice.status == InvoiceStatus.ISSUED
    # the only deferred work is the charge handed to the worker
    assert len(callbacks) == 1


def test_declined_charge_fails_invoice_and_subscription_in_the_same_transaction(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    _decline(monkeypatch)

    with django_capture_on_commit_callbacks(execute=False):
        PaymentService.attempt(invoice)

    invoice.refresh_from_db()
    subscription.refresh_from_db()
    assert invoice.status == InvoiceStatus.FAILED
    assert subscription.status == SubscriptionStatus.OVERDUE


def test_successful_charge_pays_invoice_and_reactivates_in_the_same_transaction(
    subscription, invoice, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.OVERDUE)
    _force(invoice, InvoiceStatus.FAILED)

    with django_capture_on_commit_callbacks(execute=False):
        PaymentService.attempt(invoice)

    invoice.refresh_from_db()
    subscription.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert subscription.status == SubscriptionStatus.ACTIVE


def test_webhook_success_pays_the_invoice_in_the_same_transaction(
    subscription, invoice, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    attempt = PaymentAttempt.objects.create(
        invoice=invoice, amount=invoice.total, status=PaymentStatus.PENDING
    )

    with django_capture_on_commit_callbacks(execute=False):
        PaymentService.handle_webhook(
            "payment.updated", {"payment_attempt_id": attempt.pk, "status": "success"}
        )

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_cancel_closes_open_invoices_in_the_same_transaction(
    subscription, invoice, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.FAILED)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        SubscriptionService.cancel(subscription)

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.CANCELED
    assert callbacks == []


def test_dunning_exhaustion_closes_the_subscription_in_the_same_transaction(
    subscription, invoice, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.OVERDUE)
    _force(invoice, InvoiceStatus.FAILED)
    for _ in range(MAX_DUNNING_ATTEMPTS):
        PaymentAttempt.objects.create(
            invoice=invoice, amount=invoice.total, status=PaymentStatus.FAILED
        )

    with django_capture_on_commit_callbacks(execute=False):
        run_dunning()

    invoice.refresh_from_db()
    subscription.refresh_from_db()
    assert invoice.status == InvoiceStatus.OVERDUE
    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.canceled_reason == CancellationReason.NON_PAYMENT


def test_reconcile_stuck_charges_an_issued_invoice_nobody_picked_up(
    subscription, invoice, django_capture_on_commit_callbacks
):
    from invoices.tasks import reconcile_stuck

    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    Invoice.objects.filter(pk=invoice.pk).update(issued_at=timezone.now() - timedelta(hours=1))

    with django_capture_on_commit_callbacks(execute=True):
        reconcile_stuck()

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1


def test_lost_charge_hand_off_surfaces_and_is_recovered(
    customer, plan, monkeypatch, django_capture_on_commit_callbacks
):
    from invoices.tasks import reconcile_stuck
    from payments.tasks import charge_invoice

    def broker_down(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(charge_invoice, "delay", broker_down)
    with pytest.raises(ConnectionError), django_capture_on_commit_callbacks(execute=True):
        SubscriptionService.create(customer, plan)

    # the state change was committed before the hand-off failed
    invoice = Invoice.objects.get(subscription__customer=customer)
    assert invoice.status == InvoiceStatus.ISSUED

    monkeypatch.undo()
    Invoice.objects.filter(pk=invoice.pk).update(issued_at=timezone.now() - timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        reconcile_stuck()

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_the_in_process_event_bus_is_gone():
    assert importlib.util.find_spec("infrastructure") is None


def test_dunning_does_not_cancel_a_subscription_whose_invoice_got_paid_meanwhile(
    subscription, invoice
):
    # run_dunning listed the invoice as FAILED; a payment settled it before its turn came
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.PAID)
    for _ in range(MAX_DUNNING_ATTEMPTS):
        PaymentAttempt.objects.create(
            invoice=invoice, amount=invoice.total, status=PaymentStatus.FAILED
        )

    _dun_invoice(invoice.pk)

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.ACTIVE


LOCK_ORDER = ["subscriptions_subscription", "invoices_invoice", "payments_paymentattempt"]


def _charge(subscription, invoice):
    PaymentService.charge_issued(invoice.pk)


def _manual_attempt(subscription, invoice):
    PaymentService.attempt(invoice)


def _webhook(subscription, invoice):
    attempt = PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total)
    PaymentService.handle_webhook(
        "payment.updated", {"payment_attempt_id": attempt.pk, "status": "success"}
    )


def _dunning_exhaustion(subscription, invoice):
    _force(invoice, InvoiceStatus.FAILED)
    for _ in range(MAX_DUNNING_ATTEMPTS):
        PaymentAttempt.objects.create(
            invoice=invoice, amount=invoice.total, status=PaymentStatus.FAILED
        )
    _dun_invoice(invoice.pk)


def _cancel(subscription, invoice):
    SubscriptionService.cancel(subscription)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="sqlite emits no FOR UPDATE")
@pytest.mark.parametrize("action", [_charge, _manual_attempt, _webhook, _dunning_exhaustion, _cancel])
def test_row_locks_are_taken_subscription_first(
    subscription, invoice, action, django_capture_on_commit_callbacks
):
    # every path that may settle shares one lock order, or two of them deadlock
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)

    with CaptureQueriesContext(connection) as ctx, django_capture_on_commit_callbacks(execute=False):
        action(subscription, invoice)

    locked = [
        re.search(r'FROM "(\w+)"', q["sql"]).group(1)
        for q in ctx.captured_queries
        if "FOR UPDATE" in q["sql"]
    ]
    first_acquisitions = list(dict.fromkeys(locked))
    ranks = [LOCK_ORDER.index(table) for table in first_acquisitions]
    assert ranks == sorted(ranks), first_acquisitions
