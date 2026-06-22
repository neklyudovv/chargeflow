from infrastructure.events import event_bus
from invoices.application.services import InvoiceService
from invoices.domain.models import InvoiceStatus
from payments.application.services import MockPaymentProvider
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.events import SubscriptionOverdue
from subscriptions.domain.models import SubscriptionStatus


def test_issued_invoice_is_charged_and_marked_paid(
    subscription, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        invoice = InvoiceService.generate_for_subscription(subscription)

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID

    payment = PaymentAttempt.objects.get(invoice=invoice)
    assert payment.status == PaymentStatus.SUCCESS


def test_failed_payment_fails_invoice_and_marks_subscription_overdue(
    subscription, monkeypatch, django_capture_on_commit_callbacks
):
    # on_invoice_failed only flips ACTIVE -> OVERDUE, so start from ACTIVE
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.save(update_fields=["status"])
    monkeypatch.setattr(MockPaymentProvider, "charge", lambda amount, currency: False)

    with django_capture_on_commit_callbacks(execute=True):
        invoice = InvoiceService.generate_for_subscription(subscription)

    invoice.refresh_from_db()
    subscription.refresh_from_db()
    assert invoice.status == InvoiceStatus.FAILED
    assert subscription.status == SubscriptionStatus.OVERDUE


def test_marking_subscription_overdue_publishes_event(
    subscription, django_capture_on_commit_callbacks
):
    # going overdue must announce itself on the bus, so downstream reactions
    # notifications, access limits can hook in later
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.save(update_fields=["status"])
    published = []
    event_bus.subscribe(SubscriptionOverdue, published.append)

    with django_capture_on_commit_callbacks(execute=True):
        SubscriptionService.mark_overdue(subscription)

    assert [e.subscription_id for e in published] == [subscription.pk]
