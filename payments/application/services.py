import random
from logging import getLogger

from django.conf import settings
from django.db import transaction

from invoices.application.services import InvoiceService
from invoices.domain.models import PAYABLE_INVOICE_STATUSES, Invoice, InvoiceStatus
from payments.domain.models import PaymentAttempt, PaymentStatus, WebhookEvent
from subscriptions.application.services import SubscriptionService

logger = getLogger(__name__)


class InvoiceNotPayable(Exception):
    """Raised when a payment is attempted against an invoice in a non-payable state."""


class PaymentService:
    @staticmethod
    @transaction.atomic
    def attempt(invoice: Invoice, idempotency_key: str | None = None) -> PaymentAttempt:
        # a retry carrying the same idempotency key returns
        # the original attempt instead of charging again
        if idempotency_key:
            replay = PaymentAttempt.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if replay:
                return replay
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status not in PAYABLE_INVOICE_STATUSES:
            raise InvoiceNotPayable(
                f"Cannot attempt payment on invoice in status '{invoice.status}'"
            )
        # an issued invoice is charged once per issue; a retry comes through a reissue
        if invoice.status == InvoiceStatus.ISSUED and invoice.issued_at:
            this_issue = invoice.payment_attempts.filter(
                attempted_at__gte=invoice.issued_at
            ).first()
            if this_issue:
                return this_issue
        existing = invoice.payment_attempts.filter(
            status__in=[PaymentStatus.PENDING, PaymentStatus.SUCCESS]
        ).first()
        if existing:
            # charge is already pending or has succeeded for this invoice;
            return existing
        payment = PaymentAttempt.objects.create(
            invoice=invoice,
            amount=invoice.total,
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key,
        )
        success = MockPaymentProvider.charge(invoice.total, invoice.currency)

        PaymentService.settle(payment, success, {"status": "ok" if success else "declined"})
        return payment

    @staticmethod
    @transaction.atomic
    def settle(payment: PaymentAttempt, success: bool, provider_response: dict) -> None:
        # the one consumer of a charge result: attempt -> invoice -> subscription, one transaction
        payment.status = PaymentStatus.SUCCESS if success else PaymentStatus.FAILED
        payment.provider_response = provider_response
        payment.save(update_fields=["status", "provider_response"])
        invoice = payment.invoice
        if success:
            InvoiceService.mark_paid(invoice)
            SubscriptionService.reactivate(invoice.subscription)
        else:
            InvoiceService.mark_failed(invoice)
            SubscriptionService.mark_overdue(invoice.subscription)

    @staticmethod
    @transaction.atomic
    def charge_issued(invoice_id) -> PaymentAttempt | None:
        # only the current issue is charged, decided under the lock
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        if invoice.status != InvoiceStatus.ISSUED:
            return None
        return PaymentService.attempt(invoice)

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

        payment_id = payload.get("payment_attempt_id")
        if not payment_id:
            webhook.processed = True
            webhook.save(update_fields=["processed"])
            return

        try:
            # lock order is invoice -> attempt everywhere
            invoice_id = PaymentAttempt.objects.values_list("invoice_id", flat=True).get(pk=payment_id)
            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
            payment = PaymentAttempt.objects.select_for_update().get(pk=payment_id)
        except PaymentAttempt.DoesNotExist:
            webhook.processed = True
            webhook.save(update_fields=["processed"])
            return

        status = payload.get("status")
        # late or duplicate webhook must never flip it back to failed,
        # nor reemit success. Only act while it hasnt succeeded yet.
        if payment.status != PaymentStatus.SUCCESS:
            if status == "success":
                payment.provider_response = payload
                if (
                    invoice.status in PAYABLE_INVOICE_STATUSES
                    and not invoice.payment_attempts.filter(status=PaymentStatus.SUCCESS).exists()
                ):
                    PaymentService.settle(payment, True, payload)
                else:
                    payment.needs_reconciliation = True
                    payment.save(update_fields=["provider_response", "needs_reconciliation"])
                    logger.error(
                        "Provider success for payment attempt %s cannot be applied to invoice %s (status '%s')",
                        payment.pk, invoice.pk, invoice.status,
                    )
            elif status == "failed" and payment.status != PaymentStatus.FAILED:
                PaymentService.settle(payment, False, payload)

        webhook.processed = True
        webhook.save(update_fields=["processed"])


class MockPaymentProvider:
    """Mock provider for MVP. Declines a share of charges set by
    settings.PAYMENT_FAILURE_RATE"""

    @staticmethod
    def charge(amount, currency) -> bool:
        return random.random() >= settings.PAYMENT_FAILURE_RATE
