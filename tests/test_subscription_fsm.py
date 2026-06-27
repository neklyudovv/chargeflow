import pytest

from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import (
    SUBSCRIPTION_TRANSITIONS,
    CancellationReason,
    InvalidStatusTransition,
    SubscriptionStatus,
)

ALL_STATUSES = set(SubscriptionStatus.values)

LEGAL_EDGES = [
    (src, dst)
    for src, targets in SUBSCRIPTION_TRANSITIONS.items()
    for dst in targets
]

ILLEGAL_EDGES = [
    (src, dst)
    for src in ALL_STATUSES
    for dst in ALL_STATUSES
    if src != dst and dst not in SUBSCRIPTION_TRANSITIONS.get(src, set())
]


def _force_status(subscription, status):
    """Arrange a starting state without going through the FSM."""
    subscription.status = status
    subscription.save(update_fields=["status"])


@pytest.mark.parametrize("src,dst", LEGAL_EDGES)
def test_legal_transition_succeeds(subscription, src, dst):
    _force_status(subscription, src)

    subscription.transition_to(dst)

    subscription.refresh_from_db()
    assert subscription.status == dst


@pytest.mark.parametrize("src,dst", ILLEGAL_EDGES)
def test_illegal_transition_raises(subscription, src, dst):
    _force_status(subscription, src)

    with pytest.raises(InvalidStatusTransition):
        subscription.transition_to(dst)

    subscription.refresh_from_db()
    assert subscription.status == src


def test_cancel_sets_canceled_at(subscription):
    assert subscription.canceled_at is None

    subscription.transition_to(SubscriptionStatus.CANCELED)

    subscription.refresh_from_db()
    assert subscription.canceled_at is not None


def test_canceled_is_terminal(subscription):
    _force_status(subscription, SubscriptionStatus.CANCELED)

    assert SUBSCRIPTION_TRANSITIONS[SubscriptionStatus.CANCELED] == set()
    for dst in ALL_STATUSES - {SubscriptionStatus.CANCELED}:
        with pytest.raises(InvalidStatusTransition):
            subscription.transition_to(dst)


def test_cancel_defaults_to_voluntary_reason(subscription):
    SubscriptionService.cancel(subscription)

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.canceled_reason == CancellationReason.VOLUNTARY


def test_cancel_records_non_payment_reason(subscription):
    SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)

    subscription.refresh_from_db()
    assert subscription.canceled_reason == CancellationReason.NON_PAYMENT


def test_overdue_can_recover_to_active(subscription):
    _force_status(subscription, SubscriptionStatus.OVERDUE)

    subscription.transition_to(SubscriptionStatus.ACTIVE)

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.ACTIVE
