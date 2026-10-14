"""Locked tests: every status transition re-checks the row it is about to
change, so a stale read can never overwrite a newer state, and a provider
success that can no longer be applied is flagged instead of applied.

Inverted from the adversarial review probes of b9e2e09 (review of 2026-10).
Races are modelled deterministically: either the service is handed an instance
read before a concurrent change, or the concurrent change is injected between
the service's read and its write. READ COMMITTED produces the same interleavings.
"""
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from invoices.tasks import generate_invoice_for_subscription, run_dunning
from payments.application.services import MockPaymentProvider, PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus
from payments.tasks import attempt_payment_for_invoice
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import (
    InvalidStatusTransition,
    Subscription,
    SubscriptionStatus,
)
from subscriptions.tasks import mark_subscription_active_for_invoice, run_renewals


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


# AC1: dunning that read a FAILED invoice before the customer canceled must not
# reissue (and charge) the now-CANCELED invoice.
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


# AC2: a late success paid the invoice between dunning's read and its write;
# the stale reissue must not walk a PAID invoice back to ISSUED.
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


# AC3: a cancel that lands between the reactivation's read and its write wins;
# the subscription stays CANCELED (with its canceled_at) and is never renewed.
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
        mark_subscription_active_for_invoice(invoice.pk)
    except InvalidStatusTransition:
        # Refusing the overwrite is the point; raising is an acceptable way to do it.
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


# AC4: a billing event delivered after the subscription left ACTIVE does not
# issue an invoice. TRIAL is already skipped today; CANCELED and OVERDUE are not.
@pytest.mark.parametrize("status", [SubscriptionStatus.CANCELED, SubscriptionStatus.OVERDUE])
def test_no_invoice_is_generated_for_a_non_active_subscription(
    subscription, status, django_capture_on_commit_callbacks
):
    _force(subscription, status)

    with django_capture_on_commit_callbacks(execute=True):
        generate_invoice_for_subscription(subscription.pk)

    assert not Invoice.objects.filter(subscription=subscription).exists()


# AC5: a provider success for an invoice that was canceled in the meantime is
# not applied and not swallowed: the attempt is flagged for reconciliation.
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


# AC6: at most one SUCCESS per invoice. A late success for an older attempt,
# after a newer attempt already paid the invoice, is flagged, not applied.
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


# AC6 edge: the invariant is structural, not a convention in one service.
def test_database_rejects_a_second_success_attempt_per_invoice(invoice):
    _force(invoice, InvoiceStatus.ISSUED)
    PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total, status=PaymentStatus.SUCCESS)

    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAttempt.objects.create(
            invoice=invoice, amount=invoice.total, status=PaymentStatus.SUCCESS
        )


# AC7: a re-delivered "invoice issued" event charges only an ISSUED invoice.
# FAILED and OVERDUE invoices are retried by dunning or by hand, never by a
# duplicate delivery.
def test_redelivered_issued_event_does_not_charge_failed_or_overdue_invoice(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    _force(invoice, InvoiceStatus.ISSUED)
    _decline(monkeypatch)

    # The first delivery: the decline fails the invoice through the event chain.
    with django_capture_on_commit_callbacks(execute=True):
        attempt_payment_for_invoice(invoice.pk)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.FAILED

    attempt_payment_for_invoice(invoice.pk)
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1

    _force(invoice, InvoiceStatus.OVERDUE)
    attempt_payment_for_invoice(invoice.pk)
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1


# AC8: two deliveries of the same "invoice issued" event, the second arriving
# before the first one's outcome hop (mark_invoice_failed) has run. The invoice
# is still ISSUED, so the status check alone does not help: an issued invoice
# is charged at most once per issue.
def test_concurrent_redelivery_before_the_failure_hop_charges_once(
    subscription, invoice, monkeypatch, django_capture_on_commit_callbacks
):
    _force(subscription, SubscriptionStatus.ACTIVE)
    invoice.transition_to(InvoiceStatus.ISSUED)
    _decline(monkeypatch)

    # first delivery: the charge is declined, but its PaymentFailed callback is
    # captured and never executed, so the invoice stays ISSUED
    with django_capture_on_commit_callbacks(execute=False):
        attempt_payment_for_invoice(invoice.pk)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.ISSUED
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1

    with django_capture_on_commit_callbacks(execute=True):
        attempt_payment_for_invoice(invoice.pk)

    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1
