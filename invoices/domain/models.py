from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from subscriptions.domain.models import Subscription, InvalidStatusTransition


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    OVERDUE = "overdue", "Overdue"
    CANCELED = "canceled", "Canceled"


# Statuses from which a payment attempt is allowed (initial charge or retry).
PAYABLE_INVOICE_STATUSES: set[str] = {
    InvoiceStatus.ISSUED,
    InvoiceStatus.FAILED,
    InvoiceStatus.OVERDUE,
}


INVOICE_TRANSITIONS: dict[str, set[str]] = {
    InvoiceStatus.DRAFT: {InvoiceStatus.ISSUED, InvoiceStatus.CANCELED},
    InvoiceStatus.ISSUED: {InvoiceStatus.PAID, InvoiceStatus.FAILED, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELED},
    InvoiceStatus.FAILED: {InvoiceStatus.ISSUED, InvoiceStatus.PAID, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELED},
    InvoiceStatus.OVERDUE: {InvoiceStatus.PAID, InvoiceStatus.CANCELED},
    InvoiceStatus.PAID: set(),
    InvoiceStatus.CANCELED: set(),
}


class Invoice(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="invoices"
    )
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.DRAFT,
    )
    total = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "invoices"
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "period_start", "period_end"],
                name="unique_invoice_per_subscription_period",
            ),
        ]

    def clean(self):
        if self.subscription_id is None:
            return
        organization_id = self.subscription.organization_id
        if self.organization_id is not None and self.organization_id != organization_id:
            raise ValidationError("Organization must match the parent record.")
        if self.pk and Invoice.objects.filter(pk=self.pk).exclude(organization_id=organization_id).exists():
            raise ValidationError("Cannot move a billing record to another organization.")
        self.organization_id = organization_id

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invoice #{self.pk} - {self.subscription.customer} ({self.status})"

    def transition_to(self, new_status: str) -> None:
        allowed = INVOICE_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStatusTransition(
                f"Cannot transition from '{self.status}' to '{new_status}'"
            )
        fields = {"status": new_status, "updated_at": timezone.now()}
        if new_status == InvoiceStatus.ISSUED:
            fields["issued_at"] = fields["updated_at"]
        elif new_status == InvoiceStatus.PAID:
            fields["paid_at"] = fields["updated_at"]
        # compare-and-set: refuse a row that moved since it was read
        if not Invoice.objects.filter(pk=self.pk, status=self.status).update(**fields):
            raise InvalidStatusTransition(
                f"Invoice changed from '{self.status}' underneath the transition to '{new_status}'"
            )
        for name, value in fields.items():
            setattr(self, name, value)


class InvoiceLine(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="lines"
    )
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        app_label = "invoices"

    def clean(self):
        if self.invoice_id is None:
            return
        organization_id = self.invoice.organization_id
        if self.organization_id is not None and self.organization_id != organization_id:
            raise ValidationError("Organization must match the parent record.")
        if self.pk and InvoiceLine.objects.filter(pk=self.pk).exclude(organization_id=organization_id).exists():
            raise ValidationError("Cannot move a billing record to another organization.")
        self.organization_id = organization_id

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} - {self.amount}"
