from django.db import transaction

from infrastructure.events import event_bus
from invoices.domain.models import Invoice
from payments.domain.events import PaymentFailed, PaymentSucceeded, WebhookReceived
from payments.domain.models import PaymentAttempt, PaymentStatus, WebhookEvent


class PaymentService:
    @staticmethod
    @transaction.atomic
    def attempt(invoice: Invoice) -> PaymentAttempt:
        payment = PaymentAttempt.objects.create(
            invoice=invoice,
            amount=invoice.total,
            status=PaymentStatus.PENDING,
        )
        success = MockPaymentProvider.charge(invoice.total, invoice.currency)

        if success:
            payment.status = PaymentStatus.SUCCESS
            payment.provider_response = {"status": "ok"}
            payment.save(update_fields=["status", "provider_response"])
            event_bus.publish(PaymentSucceeded(payment_attempt_id=payment.pk))
        else:
            payment.status = PaymentStatus.FAILED
            payment.provider_response = {"status": "declined"}
            payment.save(update_fields=["status", "provider_response"])
            event_bus.publish(PaymentFailed(payment_attempt_id=payment.pk))

        return payment

    @staticmethod
    @transaction.atomic
    def handle_webhook(event_type: str, payload: dict) -> None:
        existing = WebhookEvent.objects.filter(
            event_type=event_type,
            payload=payload,
            processed=True,
        ).exists()
        if existing:
            return

        webhook = WebhookEvent.objects.create(
            event_type=event_type,
            payload=payload,
        )
        event_bus.publish(WebhookReceived(webhook_event_id=webhook.pk))

        payment_id = payload.get("payment_attempt_id")
        if not payment_id:
            webhook.processed = True
            webhook.save(update_fields=["processed"])
            return

        try:
            payment = PaymentAttempt.objects.get(pk=payment_id)
        except PaymentAttempt.DoesNotExist:
            webhook.processed = True
            webhook.save(update_fields=["processed"])
            return

        status = payload.get("status")
        if status == "success" and payment.status != PaymentStatus.SUCCESS:
            payment.status = PaymentStatus.SUCCESS
            payment.provider_response = payload
            payment.save(update_fields=["status", "provider_response"])
            event_bus.publish(PaymentSucceeded(payment_attempt_id=payment.pk))
        elif status == "failed" and payment.status != PaymentStatus.FAILED:
            payment.status = PaymentStatus.FAILED
            payment.provider_response = payload
            payment.save(update_fields=["status", "provider_response"])
            event_bus.publish(PaymentFailed(payment_attempt_id=payment.pk))

        webhook.processed = True
        webhook.save(update_fields=["processed"])


class MockPaymentProvider:
    """Mock provider for MVP. Always succeeds."""

    @staticmethod
    def charge(amount, currency) -> bool:
        return True
