from datetime import timedelta

from django.utils import timezone

from invoices.domain.models import Invoice
from subscriptions.domain.models import SubscriptionStatus
from subscriptions.tasks import run_renewals, run_trial_activations


def _set(subscription, status, period_end):
    subscription.status = status
    subscription.current_period_end = period_end
    subscription.save(update_fields=["status", "current_period_end"])


def test_renewals_bill_an_expired_active_subscription(
    subscription, django_capture_on_commit_callbacks
):
    _set(subscription, SubscriptionStatus.ACTIVE, timezone.now() - timedelta(hours=1))

    # events reach their handlers on commit, so the billing chain only runs
    # once the callbacks fire
    with django_capture_on_commit_callbacks(execute=True):
        run_renewals()

    subscription.refresh_from_db()
    assert subscription.current_period_end > timezone.now()
    assert Invoice.objects.filter(subscription=subscription).count() == 1


def test_renewals_skip_a_subscription_mid_period(subscription):
    period_end = timezone.now() + timedelta(days=10)
    _set(subscription, SubscriptionStatus.ACTIVE, period_end)

    run_renewals()

    subscription.refresh_from_db()
    assert subscription.current_period_end == period_end
    assert not Invoice.objects.filter(subscription=subscription).exists()


def test_renewals_skip_an_overdue_subscription(subscription):
    # dunning owns OVERDUE subscriptions - renewing would bill a customer
    # who has not paid for the period they already have
    _set(subscription, SubscriptionStatus.OVERDUE, timezone.now() - timedelta(hours=1))

    run_renewals()

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.OVERDUE
    assert not Invoice.objects.filter(subscription=subscription).exists()


def test_expired_trial_activates_and_bills(
    subscription, django_capture_on_commit_callbacks
):
    _set(subscription, SubscriptionStatus.TRIAL, timezone.now() - timedelta(hours=1))

    with django_capture_on_commit_callbacks(execute=True):
        run_trial_activations()

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert Invoice.objects.filter(subscription=subscription).count() == 1


def test_running_trial_is_left_alone(subscription):
    _set(subscription, SubscriptionStatus.TRIAL, timezone.now() + timedelta(days=5))

    run_trial_activations()

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.TRIAL
    assert not Invoice.objects.filter(subscription=subscription).exists()


def test_renewals_are_idempotent_within_a_cycle(
    subscription, django_capture_on_commit_callbacks
):
    # a second cycle before the new period ends must not bill twice
    _set(subscription, SubscriptionStatus.ACTIVE, timezone.now() - timedelta(hours=1))

    with django_capture_on_commit_callbacks(execute=True):
        run_renewals()
        run_renewals()

    assert Invoice.objects.filter(subscription=subscription).count() == 1
