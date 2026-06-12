from invoices.domain.models import InvoiceStatus
from invoices.tasks import run_dunning
from subscriptions.domain.models import SubscriptionStatus


def _force_status(invoice, status):
    invoice.status = status
    invoice.save(update_fields=["status"])


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
