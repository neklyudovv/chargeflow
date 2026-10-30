import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from invoices.domain.models import Invoice


def new_provider_idempotency_key():
    return uuid.uuid4().hex


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
    currency = models.CharField(max_length=3, default="USD")
    provider = models.CharField(max_length=50, default="mock")
    provider_idempotency_key = models.CharField(
        max_length=255, default=new_provider_idempotency_key
    )
    provider_customer_id = models.CharField(max_length=255, blank=True, default="")
    provider_payment_method_id = models.CharField(max_length=255, blank=True, default="")
    provider_charge_id = models.CharField(max_length=255, blank=True, default="")
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
            models.UniqueConstraint(
                fields=["provider", "provider_idempotency_key"],
                name="unique_provider_payment_key",
            ),
            models.UniqueConstraint(
                fields=["provider", "provider_charge_id"],
                condition=~models.Q(provider_charge_id=""),
                name="unique_provider_charge",
            ),
        ]

    def clean(self):
        if self.invoice_id is None:
            return
        organization_id = Invoice.objects.values_list("organization_id", flat=True).get(pk=self.invoice_id)
        if self.organization_id is not None and self.organization_id != organization_id:
            raise ValidationError("Organization must match the parent record.")
        if self.pk:
            original = PaymentAttempt.objects.filter(pk=self.pk).values(
                "organization_id",
                "invoice_id",
                "amount",
                "currency",
                "provider",
                "provider_idempotency_key",
                "provider_customer_id",
                "provider_payment_method_id",
                "idempotency_key",
            ).first()
            if original:
                current = {
                    "organization_id": self.organization_id,
                    "invoice_id": self.invoice_id,
                    "amount": Decimal(self.amount).quantize(Decimal("0.01")),
                    "currency": self.currency,
                    "provider": self.provider,
                    "provider_idempotency_key": self.provider_idempotency_key,
                    "provider_customer_id": self.provider_customer_id,
                    "provider_payment_method_id": self.provider_payment_method_id,
                    "idempotency_key": self.idempotency_key,
                }
                original["amount"] = original["amount"].quantize(Decimal("0.01"))
                if current != original:
                    raise ValidationError("Payment request snapshots are immutable.")
        self.organization_id = organization_id

    def save(self, *args, **kwargs):
        self.idempotency_key = self.idempotency_key or None
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Payment #{self.pk} - {self.invoice} ({self.status})"


class WebhookEvent(models.Model):
    # events without a known payment cannot be assigned to an organization
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, null=True, blank=True)

    event_type = models.CharField(max_length=255)
    provider = models.CharField(max_length=50, default="mock")
    provider_event_id = models.CharField(max_length=255)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"],
                name="unique_provider_event",
            )
        ]

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


class MockChargeReceipt(models.Model):
    provider_idempotency_key = models.CharField(max_length=255, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    success = models.BooleanField()
    provider_charge_id = models.CharField(max_length=255, unique=True)
    provider_response = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"

    def save(self, *args, **kwargs):
        if self.pk and MockChargeReceipt.objects.filter(pk=self.pk).exists():
            raise ValidationError("Mock charge receipts are immutable.")
        super().save(*args, **kwargs)
