from datetime import timedelta

from django.utils import timezone

from invoices.domain.models import Invoice, InvoiceStatus
from invoices.tasks import MAX_DUNNING_ATTEMPTS, run_dunning
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.domain.models import SubscriptionStatus
from subscriptions.tasks import mark_subscription_active_for_invoice


def _force_status(obj, status):
    obj.status = status
    obj.save(update_fields=["status"])


def test_dunning_retries_failed_invoice(invoice, django_capture_on_commit_callbacks):
    _force_status(invoice, InvoiceStatus.FAILED)

    # dunning reissues the failed invoice, which retriggers the payment flow
    with django_capture_on_commit_callbacks(execute=True):
        run_dunning()

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_dunning_leaves_non_failed_invoice_untouched(
    invoice, django_capture_on_commit_callbacks
):
    _force_status(invoice, InvoiceStatus.PAID)

    with django_capture_on_commit_callbacks(execute=True):
        run_dunning()

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_dunning_reactivates_overdue_subscription(
    subscription, invoice, django_capture_on_commit_callbacks
):
    # subscription went overdue after its invoice failed, a successful dunning
    # retry must pay the invoice AND bring the subscription back to ACTIVE
    _force_status(subscription, SubscriptionStatus.OVERDUE)
    _force_status(invoice, InvoiceStatus.FAILED)

    with django_capture_on_commit_callbacks(execute=True):
        run_dunning()

    invoice.refresh_from_db()
    subscription.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert subscription.status == SubscriptionStatus.ACTIVE


def test_dunning_gives_up_after_retry_cap(invoice, django_capture_on_commit_callbacks):
    # An invoice that already failed the maximum number of charges is given up on:
    # it moves to the terminal OVERDUE state and is not charged again.
    _force_status(invoice, InvoiceStatus.FAILED)
    for _ in range(MAX_DUNNING_ATTEMPTS):
        PaymentAttempt.objects.create(
            invoice=invoice, amount=invoice.total, status=PaymentStatus.FAILED
        )

    with django_capture_on_commit_callbacks(execute=True):
        run_dunning()

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.OVERDUE
    # no new charge was attempted beyond the ones that exhausted the cap
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == MAX_DUNNING_ATTEMPTS


def test_reactivation_blocked_by_other_unpaid_invoice(subscription, invoice):
    # Settling one invoice must NOT reactivate the subscription while another
    # invoice for it is still unpaid.
    _force_status(subscription, SubscriptionStatus.OVERDUE)
    _force_status(invoice, InvoiceStatus.PAID)
    now = timezone.now()
    Invoice.objects.create(
        subscription=subscription,
        total="19.99",
        currency="USD",
        status=InvoiceStatus.FAILED,
        period_start=now + timedelta(days=31),
        period_end=now + timedelta(days=61),
    )

    mark_subscription_active_for_invoice(invoice.pk)

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.OVERDUE
