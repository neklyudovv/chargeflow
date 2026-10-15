"""locked tests of PR #3: every status transition re-checks its row under lock, and a provider success that cannot be applied is flagged, not applied.
races are modelled deterministically: a stale instance, or a concurrent update injected between read and write
"""
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from invoices.tasks import run_dunning
from payments.application.services import MockPaymentProvider, PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus
from payments.tasks import charge_invoice
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import (
    InvalidStatusTransition,
    Subscription,
    SubscriptionStatus,
)
from subscriptions.tasks import run_renewals


def _force(obj, status):
    obj.status = status
    obj.save(update_fields=["status"])


def _decline(monkeypatch):
    monkeypatch.setattr(MockPaymentProvider, "charge", staticmethod(lambda a, c: False))


def _accept(monkeypatch):
    monkeypatch.setattr(MockPaymentProvider, "charge", staticmethod(lambda a, c: True))


def _webhook_success(attempt):
    PaymentService.handle_webhook(
        "payment.updated", {"payment_attempt_id": attempt.pk, "status": "success"}
    )


def test_reissue_of_stale_failed_invoice_after_cancel_is_a_noop(
    subscription, invoice, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.FAILED)
    stale = Invoice.objects.get(pk=invoice.pk)

    with django_capture_on_commit_callbacks(execute=True):
        SubscriptionService.cancel(Subscription.objects.get(pk=subscription.pk))
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.CANCELED

    with django_capture_on_commit_callbacks(execute=True):
        InvoiceService.reissue_for_retry(stale)

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.CANCELED
    assert not PaymentAttempt.objects.filter(invoice=invoice).exists()


def test_reissue_of_stale_failed_invoice_after_payment_keeps_it_paid(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    _decline(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True):
        p1 = PaymentService.attempt(invoice)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.FAILED
    stale = Invoice.objects.get(pk=invoice.pk)

    with django_capture_on_commit_callbacks(execute=True):
        _webhook_success(p1)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    paid_at = invoice.paid_at
    assert paid_at is not None

    with django_capture_on_commit_callbacks(execute=True):
        InvoiceService.reissue_for_retry(stale)

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.paid_at == paid_at
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1


# the cancel lands between the reactivation's read and its write
def test_reactivation_racing_a_cancel_does_not_resurrect_the_subscription(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.OVERDUE)
    _force(invoice, InvoiceStatus.PAID)
    original = Subscription.transition_to
    fired = []

    def racing_transition(self, new_status):
        if not fired:
            fired.append(1)
            Subscription.objects.filter(pk=self.pk).update(
                status=SubscriptionStatus.CANCELED,
                canceled_at=timezone.now(),
                canceled_reason="voluntary",
            )
        return original(self, new_status)

    monkeypatch.setattr(Subscription, "transition_to", racing_transition)
    try:
        SubscriptionService.reactivate(subscription)
    except InvalidStatusTransition:
        # refusing the overwrite is the point, raising is one way to do it
        pass
    monkeypatch.setattr(Subscription, "transition_to", original)

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.canceled_at is not None

    Subscription.objects.filter(pk=subscription.pk).update(
        current_period_end=timezone.now() - timedelta(minutes=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        run_renewals()
    assert Invoice.objects.filter(subscription=subscription).count() == 1


@pytest.mark.parametrize("status", [SubscriptionStatus.CANCELED, SubscriptionStatus.OVERDUE])
def test_no_invoice_is_generated_for_a_non_active_subscription(
    subscription, status, django_capture_on_commit_callbacks
):
    _force(subscription, status)

    with django_capture_on_commit_callbacks(execute=True):
        InvoiceService.generate_for_subscription(subscription)

    assert not Invoice.objects.filter(subscription=subscription).exists()


def test_late_success_on_canceled_invoice_is_flagged_not_applied(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    _decline(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True):
        p1 = PaymentService.attempt(invoice)
    with django_capture_on_commit_callbacks(execute=True):
        SubscriptionService.cancel(Subscription.objects.get(pk=subscription.pk))
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.CANCELED

    with django_capture_on_commit_callbacks(execute=True):
        _webhook_success(p1)

    p1.refresh_from_db()
    invoice.refresh_from_db()
    assert p1.status == PaymentStatus.FAILED
    assert p1.needs_reconciliation is True
    assert invoice.status == InvoiceStatus.CANCELED
    assert not PaymentAttempt.objects.filter(invoice=invoice, status=PaymentStatus.SUCCESS).exists()


def test_second_success_for_an_invoice_is_flagged_not_applied(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    _decline(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True):
        p1 = PaymentService.attempt(invoice)
    _accept(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True):
        run_dunning()
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID

    with django_capture_on_commit_callbacks(execute=True):
        _webhook_success(p1)

    p1.refresh_from_db()
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert p1.status == PaymentStatus.FAILED
    assert p1.needs_reconciliation is True
    assert PaymentAttempt.objects.filter(invoice=invoice, status=PaymentStatus.SUCCESS).count() == 1


def test_database_rejects_a_second_success_attempt_per_invoice(invoice):
    _force(invoice, InvoiceStatus.ISSUED)
    PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total, status=PaymentStatus.SUCCESS)

    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAttempt.objects.create(
            invoice=invoice, amount=invoice.total, status=PaymentStatus.SUCCESS
        )


def test_charge_task_skips_a_failed_or_overdue_invoice(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    _decline(monkeypatch)

    # the decline fails the invoice
    with django_capture_on_commit_callbacks(execute=True):
        charge_invoice(invoice.pk)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.FAILED

    charge_invoice(invoice.pk)
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1

    _force(invoice, InvoiceStatus.OVERDUE)
    charge_invoice(invoice.pk)
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1


# an issued invoice is charged once per issue
def test_charge_task_does_not_charge_the_same_issue_twice(subscription, invoice):
    _force(subscription, SubscriptionStatus.ACTIVE)
    invoice.transition_to(InvoiceStatus.ISSUED)
    PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total, status=PaymentStatus.FAILED)

    charge_invoice(invoice.pk)

    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1
