from django.db import models


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
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=20, choices=PlanStatus.choices, default=PlanStatus.ACTIVE)
    interval = models.CharField(max_length=20, choices=PlanInterval.choices, default=PlanInterval.MONTH)
    trial_period_days = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.organization})"