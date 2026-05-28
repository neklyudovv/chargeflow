from celery import shared_task
from django.db import OperationalError

from invoices.domain.models import Invoice
from subscriptions.domain.models import SubscriptionStatus

# Transient DB errors retry with backoff; logic errors surface instead of looping.
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}


@shared_task(**RETRY)
def mark_subscription_overdue_for_invoice(invoice_id):
    invoice = Invoice.objects.select_related("subscription").get(pk=invoice_id)
    subscription = invoice.subscription
    # Only ACTIVE -> OVERDUE; a re-delivered event on an already-overdue
    # subscription is a no-op, so this is safe to retry
    if subscription.status == SubscriptionStatus.ACTIVE:
        subscription.transition_to(SubscriptionStatus.OVERDUE)
