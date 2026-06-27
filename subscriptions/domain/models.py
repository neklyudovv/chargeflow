from django.db import models
from django.utils import timezone

from accounts.models import Customer
from plans.models import Plan


class SubscriptionStatus(models.TextChoices):
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    OVERDUE = "overdue", "Overdue"
    CANCELED = "canceled", "Canceled"


class CancellationReason(models.TextChoices):
    VOLUNTARY = "voluntary", "Voluntary"
    NON_PAYMENT = "non_payment", "Non-payment"


SUBSCRIPTION_TRANSITIONS: dict[str, set[str]] = {
    SubscriptionStatus.TRIAL: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELED},
    SubscriptionStatus.ACTIVE: {SubscriptionStatus.OVERDUE, SubscriptionStatus.CANCELED},
    SubscriptionStatus.OVERDUE: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELED},
    SubscriptionStatus.CANCELED: set(),
}


class InvalidStatusTransition(Exception):
    pass


class Subscription(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, related_name="subscriptions"
    )
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIAL,
    )
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_reason = models.CharField(
        max_length=20, choices=CancellationReason.choices, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "subscriptions"

    def __str__(self):
        return f"{self.customer} - {self.plan} ({self.status})"

    def transition_to(self, new_status: str) -> None:
        allowed = SUBSCRIPTION_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStatusTransition(
                f"Cannot transition from '{self.status}' to '{new_status}'"
            )
        self.status = new_status
        if new_status == SubscriptionStatus.CANCELED:
            self.canceled_at = timezone.now()
        self.save(update_fields=["status", "canceled_at", "updated_at"])
