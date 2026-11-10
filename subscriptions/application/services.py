import calendar
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import Customer, Organization
from invoices.application.services import InvoiceService
from invoices.domain.models import InvoiceStatus
from plans.models import Plan, PlanStatus
from subscriptions.domain.models import (
    CancellationReason,
    InvalidStatusTransition,
    Subscription,
    SubscriptionStatus,
)


class SubscriptionConflict(Exception):
    pass


class SubscriptionService:
    @staticmethod
    def _add_months(dt, months, anchor_day=None):
        total = dt.month - 1 + months
        year = dt.year + total // 12
        month = total % 12 + 1
        day = min(anchor_day or dt.day, calendar.monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)

    @staticmethod
    def _compute_period_end(start, plan: Plan, anchor_day=None):
        if plan.interval == "day":
            return start + timedelta(days=1)
        if plan.interval == "week":
            return start + timedelta(weeks=1)
        if plan.interval == "month":
            return SubscriptionService._add_months(start, 1, anchor_day)
        if plan.interval == "year":
            return SubscriptionService._add_months(start, 12, anchor_day)
        raise ValueError(f"Unknown billing interval '{plan.interval}'")

    @staticmethod
    @transaction.atomic
    def create(customer: Customer, plan: Plan, idempotency_key: str | None = None) -> Subscription:
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 255):
            raise ValueError("Idempotency key must contain 1 to 255 characters.")
        organization_id = customer.organization_id
        Organization.objects.select_for_update(no_key=True).get(pk=organization_id)
        customer, plan = Subscription.lock_parents(customer.pk, plan.pk)
        if customer.organization_id != organization_id or customer.organization_id != plan.organization_id:
            raise ValueError("Customer and plan must belong to the same organization.")
        if idempotency_key is not None:
            replay = Subscription.objects.filter(organization_id=organization_id, idempotency_key=idempotency_key).first()
            if replay:
                if (replay.customer_id, replay.plan_id) != (customer.pk, plan.pk):
                    raise SubscriptionConflict("Idempotency key is already bound to another subscription request.")
                replay._idempotent_replay = True
                return replay
        if Subscription.objects.filter(customer=customer, plan=plan).exclude(status=SubscriptionStatus.CANCELED).exists():
            raise SubscriptionConflict("A live subscription already exists for this customer and plan.")
        plan.full_clean(validate_unique=False, validate_constraints=False)
        if plan.status != PlanStatus.ACTIVE:
            raise ValueError(f"Cannot subscribe to a plan with status '{plan.status}'")

        now = timezone.now()
        has_trial = plan.trial_period_days > 0

        subscription = Subscription.objects.create(
            customer=customer,
            plan=plan,
            status=SubscriptionStatus.TRIAL if has_trial else SubscriptionStatus.ACTIVE,
            idempotency_key=idempotency_key,
            billing_anchor_day=now.day,
            current_period_start=now,
            current_period_end=(
                now + timedelta(days=plan.trial_period_days)
                if has_trial
                else SubscriptionService._compute_period_end(now, plan)
            ),
        )

        InvoiceService.generate_for_subscription(subscription)
        return subscription

    @staticmethod
    def is_due_for_renewal(subscription: Subscription) -> bool:
        return (
            subscription.status == SubscriptionStatus.ACTIVE
            and subscription.current_period_end <= timezone.now()
        )

    @staticmethod
    def is_trial_to_activate(subscription: Subscription) -> bool:
        return subscription.status == SubscriptionStatus.TRIAL

    @staticmethod
    @transaction.atomic
    def activate(subscription: Subscription) -> None:
        # Ends a trial, and only a trial: activation resets the billing period,
        # so letting it run on an OVERDUE subscription would wipe the unpaid
        # period and clear the arrears without anyone paying them. An OVERDUE
        # subscription goes back to ACTIVE only by paying off its invoices.
        subscription = Subscription.objects.select_for_update().select_related("plan").get(pk=subscription.pk)
        if not SubscriptionService.is_trial_to_activate(subscription):
            raise ValueError(f"Cannot activate subscription with status '{subscription.status}'")

        subscription.transition_to(SubscriptionStatus.ACTIVE)
        start = min(timezone.now(), subscription.current_period_end)
        subscription.current_period_start = start
        subscription.billing_anchor_day = start.day
        subscription.current_period_end = SubscriptionService._compute_period_end(start, subscription.plan, start.day)
        subscription.save(update_fields=["current_period_start", "current_period_end", "billing_anchor_day", "updated_at"])

        InvoiceService.generate_for_subscription(subscription)

    @staticmethod
    @transaction.atomic
    def mark_overdue(subscription: Subscription) -> None:
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status != SubscriptionStatus.ACTIVE:
            return
        subscription.transition_to(SubscriptionStatus.OVERDUE)

    @staticmethod
    @transaction.atomic
    def reactivate(subscription: Subscription) -> None:
        # only OVERDUE -> ACTIVE, and only once no failed invoices are left
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status != SubscriptionStatus.OVERDUE:
            return
        if subscription.invoices.filter(
            status__in=[InvoiceStatus.FAILED, InvoiceStatus.OVERDUE]
        ).exists():
            return
        try:
            subscription.transition_to(SubscriptionStatus.ACTIVE)
        except InvalidStatusTransition:
            # unreachable under a real lock; without one (sqlite) the newer state wins
            return

    @staticmethod
    @transaction.atomic
    def cancel(
        subscription: Subscription,
        reason: str = CancellationReason.VOLUNTARY,
    ) -> None:
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        subscription.transition_to(SubscriptionStatus.CANCELED)
        subscription.canceled_reason = reason
        subscription.save(update_fields=["canceled_reason", "updated_at"])
        InvoiceService.cancel_open_for_subscription(subscription)

    @staticmethod
    @transaction.atomic
    def renew(subscription: Subscription) -> None:
        # Locked and rechecked here rather than in the caller: renewing twice
        # bills the customer for a period they already have, and the unique
        # constraint on the invoice period does not catch it because the second
        # renewal moves the period forward.
        subscription = Subscription.objects.select_for_update().select_related("plan").get(pk=subscription.pk)
        if subscription.status != SubscriptionStatus.ACTIVE:
            raise ValueError(f"Cannot renew subscription with status '{subscription.status}'")
        if not SubscriptionService.is_due_for_renewal(subscription):
            raise ValueError("Cannot renew a subscription before its period has ended")

        anchor_day = subscription.billing_anchor_day or subscription.current_period_start.day
        start = subscription.current_period_end
        subscription.current_period_start = start
        subscription.billing_anchor_day = anchor_day
        subscription.current_period_end = SubscriptionService._compute_period_end(start, subscription.plan, anchor_day)
        subscription.save(update_fields=["current_period_start", "current_period_end", "billing_anchor_day", "updated_at"])

        InvoiceService.generate_for_subscription(subscription)
