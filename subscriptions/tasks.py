from celery import shared_task
from django.db import OperationalError

from invoices.domain.models import Invoice, InvoiceStatus
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import CancellationReason, SubscriptionStatus

# Transient DB errors retry with backoff; logic errors surface instead of looping.
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}


@shared_task(**RETRY)
def mark_subscription_overdue_for_invoice(invoice_id):
    invoice = Invoice.objects.select_related("subscription").get(pk=invoice_id)
    SubscriptionService.mark_overdue(invoice.subscription)


@shared_task(**RETRY)
def mark_subscription_active_for_invoice(invoice_id):
    invoice = Invoice.objects.select_related("subscription").get(pk=invoice_id)
    subscription = invoice.subscription
    # Recover from a dunning success: only OVERDUE -> ACTIVE, and only once the
    # subscription has no other failed invoices left. Safe to retry.
    if subscription.status != SubscriptionStatus.OVERDUE:
        return
    if subscription.invoices.filter(
        status__in=[InvoiceStatus.FAILED, InvoiceStatus.OVERDUE]
    ).exists():
        return
    subscription.transition_to(SubscriptionStatus.ACTIVE)


@shared_task(**RETRY)
def close_subscription_for_non_payment(invoice_id):
    invoice = Invoice.objects.select_related("subscription").get(pk=invoice_id)
    subscription = invoice.subscription
    # An invoice ran out of dunning retries; close its subscription for non-payment
    if subscription.status == SubscriptionStatus.CANCELED:
        return
    SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)
