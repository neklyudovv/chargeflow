from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from invoices.tasks import generate_invoice_for_subscription
from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt
from subscriptions.domain.models import SubscriptionStatus


def _force_status(obj, status):
    obj.status = status
    obj.save(update_fields=["status"])


def test_generate_task_skips_trial_subscription(subscription):
    # subscription fixture defaults to TRIAL - no invoice during trial
    generate_invoice_for_subscription(subscription.pk)

    assert not Invoice.objects.filter(subscription=subscription).exists()


def test_generate_task_creates_invoice_for_active_subscription(subscription):
    _force_status(subscription, SubscriptionStatus.ACTIVE)

    generate_invoice_for_subscription(subscription.pk)

    assert Invoice.objects.filter(subscription=subscription).count() == 1


def test_generate_is_idempotent_per_period(subscription):
    # re-delivered event / retried task must not create a second invoice
    # for the same billing period
    first = InvoiceService.generate_for_subscription(subscription)
    second = InvoiceService.generate_for_subscription(subscription)

    assert first.pk == second.pk
    assert Invoice.objects.filter(subscription=subscription).count() == 1


def test_mark_paid_is_idempotent(invoice):
    _force_status(invoice, InvoiceStatus.ISSUED)

    InvoiceService.mark_paid(invoice)
    InvoiceService.mark_paid(invoice) # second call must not raise

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_attempt_does_not_stack_duplicate(invoice):
    _force_status(invoice, InvoiceStatus.ISSUED)

    first = PaymentService.attempt(invoice)
    second = PaymentService.attempt(invoice)

    assert first.pk == second.pk
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1
