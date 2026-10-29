from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
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
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)

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

    def clean(self):
        if self.customer_id is None or self.plan_id is None:
            return
        organization_id = Customer.objects.values_list("organization_id", flat=True).get(pk=self.customer_id)
        if Plan.objects.values_list("organization_id", flat=True).get(pk=self.plan_id) != organization_id:
            raise ValidationError("Customer and plan must belong to the same organization.")
        if self.organization_id is not None and self.organization_id != organization_id:
            raise ValidationError("Organization must match the parent record.")
        if self.pk and Subscription.objects.filter(pk=self.pk).exclude(organization_id=organization_id).exists():
            raise ValidationError("Cannot move a billing record to another organization.")
        self.organization_id = organization_id

    @staticmethod
    def lock_parents(customer_id, plan_id):
        # shared locks permit sibling subscriptions but exclude parent organization moves
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM accounts_customer WHERE id = %s FOR SHARE", [customer_id])
            cursor.execute("SELECT id FROM plans_plan WHERE id = %s FOR SHARE", [plan_id])
        return Customer.objects.get(pk=customer_id), Plan.objects.get(pk=plan_id)

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.lock_parents(self.customer_id, self.plan_id)
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer} - {self.plan} ({self.status})"

    def transition_to(self, new_status: str) -> None:
        allowed = SUBSCRIPTION_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStatusTransition(
                f"Cannot transition from '{self.status}' to '{new_status}'"
            )
        fields = {"status": new_status, "updated_at": timezone.now()}
        if new_status == SubscriptionStatus.CANCELED:
            fields["canceled_at"] = fields["updated_at"]
        # compare-and-set: refuse a row that moved since it was read
        if not Subscription.objects.filter(pk=self.pk, status=self.status).update(**fields):
            raise InvalidStatusTransition(
                f"Subscription changed from '{self.status}' underneath the transition to '{new_status}'"
            )
        for name, value in fields.items():
            setattr(self, name, value)
