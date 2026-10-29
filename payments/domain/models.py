from django.core.exceptions import ValidationError
from django.db import models

from invoices.domain.models import Invoice


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class PaymentAttempt(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="payment_attempts"
    )
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    provider_response = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(
        max_length=255, null=True, blank=True
    )
    attempted_at = models.DateTimeField(auto_now_add=True)
    # a provider success that could not be applied to the invoice: money may need a refund
    needs_reconciliation = models.BooleanField(default=False, db_default=False)

    class Meta:
        app_label = "payments"
        constraints = [
            models.UniqueConstraint(fields=["organization", "idempotency_key"], name="unique_project_payment_key"),
            models.UniqueConstraint(
                fields=["invoice"],
                condition=models.Q(status=PaymentStatus.SUCCESS),
                name="one_success_per_invoice",
            ),
        ]

    def clean(self):
        if self.invoice_id is None:
            return
        organization_id = self.invoice.organization_id
        if self.organization_id is not None and self.organization_id != organization_id:
            raise ValidationError("Organization must match the parent record.")
        if self.pk and PaymentAttempt.objects.filter(pk=self.pk).exclude(organization_id=organization_id).exists():
            raise ValidationError("Cannot move a billing record to another organization.")
        self.organization_id = organization_id

    def save(self, *args, **kwargs):
        self.clean()
        self.idempotency_key = self.idempotency_key or None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Payment #{self.pk} - {self.invoice} ({self.status})"


class WebhookEvent(models.Model):
    # events without a known payment cannot be assigned to an organization
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, null=True, blank=True)

    event_type = models.CharField(max_length=255)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"

    def clean(self):
        payment_id = self.payload.get("payment_attempt_id") if isinstance(self.payload, dict) else None
        try:
            organization_id = PaymentAttempt.objects.get(pk=payment_id).organization_id
        except (PaymentAttempt.DoesNotExist, ValueError, TypeError):
            organization_id = None
        if self.organization_id is not None and self.organization_id != organization_id:
            raise ValidationError("Organization must match the webhook payment.")
        self.organization_id = organization_id

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.event_type} ({self.received_at})"
