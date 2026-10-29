import pytest

from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import (
    CancellationReason,
    InvalidStatusTransition,
    SubscriptionStatus,
)

ALL_STATUSES = set(SubscriptionStatus.values)

EXPECTED_TRANSITIONS = {
    SubscriptionStatus.TRIAL: {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.CANCELED,
    },
    SubscriptionStatus.ACTIVE: {
        SubscriptionStatus.OVERDUE,
        SubscriptionStatus.CANCELED,
    },
    SubscriptionStatus.OVERDUE: {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.CANCELED,
    },
    SubscriptionStatus.CANCELED: set(),
}

LEGAL_EDGES = [
    (src, dst)
    for src, targets in EXPECTED_TRANSITIONS.items()
    for dst in targets
]

ILLEGAL_EDGES = [
    (src, dst)
    for src in ALL_STATUSES
    for dst in ALL_STATUSES
    if src != dst and dst not in EXPECTED_TRANSITIONS.get(src, set())
]


def _force_status(subscription, status):
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


def test_cancel_defaults_to_voluntary_reason(subscription):
    SubscriptionService.cancel(subscription)

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.canceled_reason == CancellationReason.VOLUNTARY


def test_cancel_records_non_payment_reason(subscription):
    SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)

    subscription.refresh_from_db()
    assert subscription.canceled_reason == CancellationReason.NON_PAYMENT
