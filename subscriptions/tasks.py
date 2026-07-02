from celery import shared_task
from django.db import OperationalError, transaction
from django.utils import timezone

from invoices.domain.models import Invoice, InvoiceStatus
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import (
    CancellationReason,
    Subscription,
    SubscriptionStatus,
)

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
def run_renewals():
    # Periodic renewal cycle: roll every ACTIVE subscription whose period has
    # run out into the next one. renew() emits SubscriptionRenewed, which bills
    # the new period through the normal event flow. OVERDUE subscriptions are
    # left alone - dunning owns them until they pay or get closed.
    due_ids = list(
        Subscription.objects.filter(
            status=SubscriptionStatus.ACTIVE,
            current_period_end__lte=timezone.now(),
        ).values_list("pk", flat=True)
    )
    for subscription_id in due_ids:
        _renew_if_still_due(subscription_id)


@shared_task(**RETRY)
def run_trial_activations():
    # Periodic trial cycle: a trial that has run out becomes ACTIVE, which is
    # what triggers its first invoice (billing skips TRIAL subscriptions).
    expired_ids = list(
        Subscription.objects.filter(
            status=SubscriptionStatus.TRIAL,
            current_period_end__lte=timezone.now(),
        ).values_list("pk", flat=True)
    )
    for subscription_id in expired_ids:
        _activate_if_still_expired(subscription_id)


@transaction.atomic
def _renew_if_still_due(subscription_id):
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .get(pk=subscription_id)
    )
    # Recheck under the lock: a concurrent cycle or the API action may already
    # have moved this subscription into its next period. Renewing twice would
    # bill the customer for a period they already have.
    if subscription.status != SubscriptionStatus.ACTIVE:
        return
    if subscription.current_period_end > timezone.now():
        return
    SubscriptionService.renew(subscription)


@transaction.atomic
def _activate_if_still_expired(subscription_id):
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .get(pk=subscription_id)
    )
    if subscription.status != SubscriptionStatus.TRIAL:
        return
    if subscription.current_period_end > timezone.now():
        return
    SubscriptionService.activate(subscription)


@shared_task(**RETRY)
def close_subscription_for_non_payment(invoice_id):
    invoice = Invoice.objects.select_related("subscription").get(pk=invoice_id)
    subscription = invoice.subscription
    # An invoice ran out of dunning retries; close its subscription for non-payment
    if subscription.status == SubscriptionStatus.CANCELED:
        return
    SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)
