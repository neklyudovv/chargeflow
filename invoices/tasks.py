from celery import shared_task
from django.db import OperationalError

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.domain.models import Subscription, SubscriptionStatus

# db errors retry with backoff
# logic errors are not listed here, so they surface instead of looping forever
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}

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
def run_dunning():
    # Periodic dunning cycle: retry every failed invoice by reissuing it,
    # which retriggers a payment attempt through the normal event flow. 
    failed_ids = list(
        Invoice.objects.filter(status=InvoiceStatus.FAILED).values_list("pk", flat=True)
    )
    for invoice_id in failed_ids:
        invoice = Invoice.objects.get(pk=invoice_id)
        failed_charges = invoice.payment_attempts.filter(
            status=PaymentStatus.FAILED
        ).count()
        if failed_charges >= MAX_DUNNING_ATTEMPTS:
            continue  # dunning exhausted — stop charging this invoice
        InvoiceService.reissue_for_retry(invoice)
