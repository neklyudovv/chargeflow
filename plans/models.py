from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from plans.validators import validate_currency


class PlanStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class PlanInterval(models.TextChoices):
    DAY = "day", "Day"
    WEEK = "week", "Week"
    MONTH = "month", "Month"
    YEAR = "year", "Year"


class Plan(models.Model):
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="plans"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(default="", blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    currency = models.CharField(max_length=3, default="USD", validators=[validate_currency])
    status = models.CharField(max_length=20, choices=PlanStatus.choices, default=PlanStatus.ACTIVE)
    interval = models.CharField(max_length=20, choices=PlanInterval.choices, default=PlanInterval.MONTH)
    trial_period_days = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=0), name="plan_price_nonnegative"),
            models.CheckConstraint(condition=models.Q(trial_period_days__gte=0), name="plan_trial_nonnegative"),
        ]

    def clean(self):
        if self.pk and Plan.objects.filter(pk=self.pk).exclude(organization_id=self.organization_id).exists():
            if self.subscriptions.exists():
                raise ValidationError("Cannot move a billing parent to another organization.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            Plan.objects.select_for_update(no_key=True).filter(pk=self.pk).first()
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.organization})"
