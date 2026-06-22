import calendar
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
    SubscriptionOverdue,
    SubscriptionRenewed,
)
from subscriptions.domain.models import Subscription, SubscriptionStatus


class SubscriptionService:
    @staticmethod
    def _add_months(dt, months):
        total = dt.month - 1 + months
        year = dt.year + total // 12
        month = total % 12 + 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)

    @staticmethod
    def _compute_period_end(start, plan: Plan):
        if plan.interval == "day":
            return start + timedelta(days=1)
        if plan.interval == "week":
            return start + timedelta(weeks=1)
        if plan.interval == "month":
            return SubscriptionService._add_months(start, 1)
        if plan.interval == "year":
            return SubscriptionService._add_months(start, 12)
        return start + timedelta(days=30)

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
    def mark_overdue(subscription: Subscription) -> None:
        if subscription.status != SubscriptionStatus.ACTIVE:
            return
        subscription.transition_to(SubscriptionStatus.OVERDUE)
        event_bus.publish(SubscriptionOverdue(subscription_id=subscription.pk))

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
