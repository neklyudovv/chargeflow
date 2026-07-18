from logging import getLogger

from celery import shared_task
from django.db import OperationalError

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.domain.models import Subscription, SubscriptionStatus

# db errors retry with backoff
# logic errors are not listed here, so they surface instead of looping forever
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}

logger = getLogger(__name__)

MAX_DUNNING_ATTEMPTS = 3


@shared_task(**RETRY)
def generate_invoice_for_subscription(subscription_id):
    subscription = Subscription.objects.select_related("plan").get(pk=subscription_id)
    if subscription.status == SubscriptionStatus.TRIAL:
        return
    InvoiceService.generate_for_subscription(subscription)


@shared_task(**RETRY)
def mark_invoice_paid(payment_attempt_id):
    payment = PaymentAttempt.objects.select_related("invoice").get(pk=payment_attempt_id)
    InvoiceService.mark_paid(payment.invoice)


@shared_task(**RETRY)
def mark_invoice_failed(payment_attempt_id):
    payment = PaymentAttempt.objects.select_related("invoice").get(pk=payment_attempt_id)
    InvoiceService.mark_failed(payment.invoice)


@shared_task(**RETRY)
def cancel_open_invoices_for_subscription(subscription_id):
    subscription = Subscription.objects.get(pk=subscription_id)
    InvoiceService.cancel_open_for_subscription(subscription)


@shared_task(**RETRY)
def run_dunning():
    # Periodic dunning cycle: retry every failed invoice by reissuing it,
    # which retriggers a payment attempt through the normal event flow. 
    failed_ids = list(
        Invoice.objects.filter(status=InvoiceStatus.FAILED).values_list("pk", flat=True)
    )
    # One bad invoice must not hold up the rest of the batch; the cycle is
    # hourly and every step rechecks its own preconditions, so a skipped
    # failure is only deferred to the next cycle.
    for invoice_id in failed_ids:
        try:
            _dun_invoice(invoice_id)
        except OperationalError:
            # transient db trouble - let the task-level retry take the batch
            raise
        except Exception:
            logger.exception("Dunning failed for invoice %s", invoice_id)


def _dun_invoice(invoice_id):
    invoice = Invoice.objects.get(pk=invoice_id)
    failed_charges = invoice.payment_attempts.filter(
        status=PaymentStatus.FAILED
    ).count()
    if failed_charges >= MAX_DUNNING_ATTEMPTS:
        # dunning exhausted - give up on this invoice and let the
        # subscription be closed for non-payment
        InvoiceService.mark_overdue(invoice)
        return
    InvoiceService.reissue_for_retry(invoice)
