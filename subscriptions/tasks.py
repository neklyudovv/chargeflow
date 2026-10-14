from logging import getLogger

from celery import shared_task
from django.db import OperationalError, transaction
from django.utils import timezone

from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription, SubscriptionStatus

logger = getLogger(__name__)

# Transient DB errors retry with backoff; logic errors surface instead of looping.
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}


@shared_task(**RETRY)
def run_renewals():
    # OVERDUE subscriptions are left alone: dunning owns them until they pay or get closed
    due_ids = list(
        Subscription.objects.filter(
            status=SubscriptionStatus.ACTIVE,
            current_period_end__lte=timezone.now(),
        ).values_list("pk", flat=True)
    )
    _run_batch(due_ids, _renew_if_still_due, "renew")


@shared_task(**RETRY)
def run_trial_activations():
    # activation is what bills the first invoice: billing skips TRIAL subscriptions
    expired_ids = list(
        Subscription.objects.filter(
            status=SubscriptionStatus.TRIAL,
            current_period_end__lte=timezone.now(),
        ).values_list("pk", flat=True)
    )
    _run_batch(expired_ids, _activate_if_still_expired, "activate")


def _run_batch(subscription_ids, handler, action):
    # One bad subscription must not hold up the rest of the batch: the cycle is
    # hourly, and every item rechecks its own preconditions, so skipping a
    # failure here only defers that one subscription to the next cycle.
    for subscription_id in subscription_ids:
        try:
            handler(subscription_id)
        except OperationalError:
            # transient db trouble - let the task-level retry take the batch
            raise
        except Exception:
            logger.exception("Failed to %s subscription %s", action, subscription_id)


@transaction.atomic
def _renew_if_still_due(subscription_id):
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .get(pk=subscription_id)
    )
    # Recheck under the lock: a concurrent cycle or the API action may already
    # have moved this subscription into its next period.
    if not SubscriptionService.is_due_for_renewal(subscription):
        return
    SubscriptionService.renew(subscription)


@transaction.atomic
def _activate_if_still_expired(subscription_id):
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .get(pk=subscription_id)
    )
    if not SubscriptionService.is_trial_to_activate(subscription):
        return
    # Trial expiry is this cycle's rule, not a service invariant - activating a
    # running trial early is a legitimate thing to do through the API.
    if subscription.current_period_end > timezone.now():
        return
    SubscriptionService.activate(subscription)

