from django.db import models

from invoices.domain.models import Invoice


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class PaymentAttempt(models.Model):
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
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"

    def __str__(self):
        return f"Payment #{self.pk} — {self.invoice} ({self.status})"


class WebhookEvent(models.Model):
    event_type = models.CharField(max_length=255)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "payments"

    def __str__(self):
        return f"{self.event_type} ({self.received_at})"
