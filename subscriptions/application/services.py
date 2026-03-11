from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import Customer
from infrastructure.events import event_bus
from plans.models import Plan, PlanStatus
from subscriptions.domain.events import (
    SubscriptionActivated,
    SubscriptionCanceled,
    SubscriptionCreated,
    SubscriptionRenewed,
)
from subscriptions.domain.models import Subscription, SubscriptionStatus


class SubscriptionService:
    @staticmethod
    def _compute_period_end(start, plan: Plan):
        deltas = {
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": timedelta(days=30),
            "year": timedelta(days=365),
        }
        return start + deltas.get(plan.interval, timedelta(days=30))

    @staticmethod
    @transaction.atomic
    def create(customer: Customer, plan: Plan) -> Subscription:
        if plan.status != PlanStatus.ACTIVE:
            raise ValueError(f"Cannot subscribe to a plan with status '{plan.status}'")

        now = timezone.now()
        has_trial = plan.trial_period_days > 0

        subscription = Subscription.objects.create(
            customer=customer,
            plan=plan,
            status=SubscriptionStatus.TRIAL if has_trial else SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=(
                now + timedelta(days=plan.trial_period_days)
                if has_trial
                else SubscriptionService._compute_period_end(now, plan)
            ),
        )

        event_bus.publish(SubscriptionCreated(subscription_id=subscription.pk))
        return subscription

    @staticmethod
    @transaction.atomic
    def activate(subscription: Subscription) -> None:
        subscription.transition_to(SubscriptionStatus.ACTIVE)
        now = timezone.now()
        subscription.current_period_start = now
        subscription.current_period_end = SubscriptionService._compute_period_end(now, subscription.plan)
        subscription.save(update_fields=["current_period_start", "current_period_end", "updated_at"])

        event_bus.publish(SubscriptionActivated(subscription_id=subscription.pk))

    @staticmethod
    @transaction.atomic
    def cancel(subscription: Subscription) -> None:
        subscription.transition_to(SubscriptionStatus.CANCELED)
        event_bus.publish(SubscriptionCanceled(subscription_id=subscription.pk))

    @staticmethod
    @transaction.atomic
    def renew(subscription: Subscription) -> None:
        if subscription.status != SubscriptionStatus.ACTIVE:
            raise ValueError(f"Cannot renew subscription with status '{subscription.status}'")

        now = timezone.now()
        subscription.current_period_start = now
        subscription.current_period_end = SubscriptionService._compute_period_end(now, subscription.plan)
        subscription.save(update_fields=["current_period_start", "current_period_end", "updated_at"])

        event_bus.publish(SubscriptionRenewed(subscription_id=subscription.pk))
