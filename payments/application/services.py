import uuid
from logging import getLogger

from django.db import IntegrityError, connection, transaction

from accounts.models import Organization
from invoices.application.services import InvoiceService
from invoices.domain.models import PAYABLE_INVOICE_STATUSES, Invoice, InvoiceStatus
from payments.application.gateway import ChargeRequest
from payments.domain.models import PaymentAttempt, PaymentStatus, WebhookEvent
from payments.infrastructure.mock_gateway import MockPaymentGateway
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription, SubscriptionStatus

logger = getLogger(__name__)


class InvoiceNotPayable(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


_GATEWAYS = {"mock": MockPaymentGateway()}


def get_payment_gateway(provider: str):
    try:
        return _GATEWAYS[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown payment provider '{provider}'.") from exc


class PaymentService:
    @staticmethod
    def attempt(invoice: Invoice, idempotency_key: str | None = None) -> PaymentAttempt:
        if connection.in_atomic_block:
            raise RuntimeError("Payment charging cannot run inside an atomic transaction.")
        payment, should_charge = PaymentService._prepare(
            invoice.pk, idempotency_key=idempotency_key
        )
        if should_charge:
            return PaymentService._charge(payment.pk)
        return payment

    @staticmethod
    @transaction.atomic
    def _prepare(
        invoice_id: int,
        idempotency_key: str | None = None,
        issued_only: bool = False,
    ) -> tuple[PaymentAttempt | None, bool]:
        subscription_id = Invoice.objects.values_list(
            "subscription_id", flat=True
        ).get(pk=invoice_id)
        subscription = Subscription.objects.select_for_update().get(pk=subscription_id)
        invoice = (
            Invoice.objects.select_for_update(of=("self",))
            .select_related("subscription__customer")
            .get(pk=invoice_id)
        )
        if issued_only and invoice.status != InvoiceStatus.ISSUED:
            return None, False

        idempotency_key = idempotency_key or None
        if idempotency_key:
            Organization.objects.select_for_update(no_key=True).get(
                pk=invoice.organization_id
            )
            replay = PaymentAttempt.objects.filter(
                organization_id=invoice.organization_id,
                idempotency_key=idempotency_key,
            ).first()
            if replay:
                if replay.invoice_id != invoice.pk:
                    raise IdempotencyConflict(
                        "Idempotency key is already bound to a different invoice."
                    )
                return replay, (
                    replay.status == PaymentStatus.PENDING
                    and not replay.needs_reconciliation
                )

        if invoice.status not in PAYABLE_INVOICE_STATUSES:
            pending = invoice.payment_attempts.filter(status=PaymentStatus.PENDING).first()
            if pending:
                return pending, not pending.needs_reconciliation
            raise InvoiceNotPayable(
                f"Cannot attempt payment on invoice in status '{invoice.status}'"
            )
        if invoice.status == InvoiceStatus.ISSUED and invoice.issued_at:
            this_issue = invoice.payment_attempts.filter(
                attempted_at__gte=invoice.issued_at
            ).first()
            if this_issue:
                if idempotency_key and this_issue.idempotency_key != idempotency_key:
                    raise IdempotencyConflict(
                        "This invoice already has a payment attempt for this issue."
                    )
                return this_issue, (
                    this_issue.status == PaymentStatus.PENDING
                    and not this_issue.needs_reconciliation
                )

        existing = invoice.payment_attempts.filter(
            status__in=[PaymentStatus.PENDING, PaymentStatus.SUCCESS]
        ).first()
        if existing:
            return existing, (
                existing.status == PaymentStatus.PENDING
                and not existing.needs_reconciliation
            )

        if subscription.status == SubscriptionStatus.CANCELED:
            raise InvoiceNotPayable("Cannot charge a canceled subscription.")

        customer = invoice.subscription.customer
        payment = PaymentAttempt.objects.create(
            invoice=invoice,
            amount=invoice.total,
            currency=invoice.currency,
            provider="mock",
            provider_idempotency_key=uuid.uuid4().hex,
            provider_customer_id=customer.provider_customer_id,
            provider_payment_method_id=customer.provider_payment_method_id,
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key,
        )
        return payment, True

    @staticmethod
    def _charge(payment_id: int) -> PaymentAttempt:
        payment = PaymentAttempt.objects.get(pk=payment_id)
        if payment.status != PaymentStatus.PENDING or payment.needs_reconciliation:
            return payment
        request = ChargeRequest(
            payment.amount,
            payment.currency,
            payment.provider_customer_id,
            payment.provider_payment_method_id,
            payment.provider_idempotency_key,
        )
        result = get_payment_gateway(payment.provider).charge(request)
        PaymentService.settle(
            payment,
            result.success,
            result.provider_response,
            provider_charge_id=result.provider_charge_id,
        )
        return PaymentAttempt.objects.get(pk=payment_id)

    @staticmethod
    @transaction.atomic
    def settle(
        payment: PaymentAttempt,
        success: bool,
        provider_response: dict,
        provider_charge_id: str = "",
    ) -> None:
        subscription_id = PaymentAttempt.objects.values_list(
            "invoice__subscription_id", flat=True
        ).get(pk=payment.pk)
        subscription = Subscription.objects.select_for_update().get(pk=subscription_id)
        invoice_id = PaymentAttempt.objects.values_list(
            "invoice_id", flat=True
        ).get(pk=payment.pk)
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        payment = PaymentAttempt.objects.select_for_update().get(pk=payment.pk)
        incoming_charge_id = provider_charge_id or provider_response.get(
            "provider_charge_id", ""
        )

        if not success and (
            payment.status == PaymentStatus.SUCCESS or payment.needs_reconciliation
        ):
            return

        if payment.provider_charge_id and incoming_charge_id and (
            payment.provider_charge_id != incoming_charge_id
        ):
            payment.needs_reconciliation = True
            payment.provider_response = {
                "previous": payment.provider_response,
                "reconciliation": provider_response,
                "reconciliation_provider_charge_id": incoming_charge_id,
            }
            payment.save(
                update_fields=["provider_response", "needs_reconciliation"]
            )
            logger.error("Provider charge mismatch for payment attempt %s", payment.pk)
            return
        if payment.status == PaymentStatus.SUCCESS or payment.needs_reconciliation:
            return

        payment.provider_response = provider_response
        if incoming_charge_id and not payment.provider_charge_id:
            payment.provider_charge_id = incoming_charge_id
            try:
                with transaction.atomic():
                    payment.save(
                        update_fields=["provider_response", "provider_charge_id"]
                    )
            except IntegrityError as exc:
                cause = exc.__cause__
                constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
                if constraint != "unique_provider_charge":
                    raise
                payment.provider_charge_id = ""
                payment.provider_response = {
                    "reconciliation": provider_response,
                    "reconciliation_provider_charge_id": incoming_charge_id,
                }
                payment.needs_reconciliation = True
                payment.save(
                    update_fields=["provider_response", "needs_reconciliation"]
                )
                logger.error(
                    "Provider charge %s is already bound to another payment",
                    incoming_charge_id,
                )
                return

        if success:
            another_success = invoice.payment_attempts.filter(
                status=PaymentStatus.SUCCESS
            ).exclude(pk=payment.pk).exists()
            applicable = (
                subscription.status != SubscriptionStatus.CANCELED
                and invoice.status in PAYABLE_INVOICE_STATUSES
                and not another_success
            )
            if not applicable:
                payment.needs_reconciliation = True
                payment.save(
                    update_fields=[
                        "provider_response",
                        "provider_charge_id",
                        "needs_reconciliation",
                    ]
                )
                logger.error(
                    "Provider success for payment attempt %s could not be applied",
                    payment.pk,
                )
                return
            payment.status = PaymentStatus.SUCCESS
            payment.save(
                update_fields=["status", "provider_response", "provider_charge_id"]
            )
            InvoiceService.mark_paid(invoice)
            SubscriptionService.reactivate(subscription)
            return

        payment.status = PaymentStatus.FAILED
        payment.save(
            update_fields=["status", "provider_response", "provider_charge_id"]
        )
        if (
            invoice.status in PAYABLE_INVOICE_STATUSES
            and subscription.status != SubscriptionStatus.CANCELED
            and not invoice.payment_attempts.filter(status=PaymentStatus.SUCCESS).exists()
        ):
            InvoiceService.mark_failed(invoice)
            SubscriptionService.mark_overdue(subscription)

    @staticmethod
    def charge_issued(invoice_id) -> PaymentAttempt | None:
        if connection.in_atomic_block:
            raise RuntimeError("Payment charging cannot run inside an atomic transaction.")
        payment, should_charge = PaymentService._prepare(invoice_id, issued_only=True)
        if should_charge:
            return PaymentService._charge(payment.pk)
        return payment

    @staticmethod
    def recover_pending(payment_id: int) -> PaymentAttempt | None:
        if connection.in_atomic_block:
            raise RuntimeError("Payment charging cannot run inside an atomic transaction.")
        payment = PaymentAttempt.objects.filter(
            pk=payment_id,
            status=PaymentStatus.PENDING,
            needs_reconciliation=False,
        ).first()
        if payment is None:
            return None
        return PaymentService._charge(payment.pk)

    @staticmethod
    @transaction.atomic
    def handle_webhook(
        event_type: str,
        payload: dict,
        *,
        provider: str,
        provider_event_id: str,
    ) -> None:
        get_payment_gateway(provider)
        if (
            not isinstance(provider_event_id, str)
            or not provider_event_id
            or len(provider_event_id) > 255
        ):
            raise ValueError("provider_event_id is required.")
        if not isinstance(event_type, str) or not event_type or len(event_type) > 255:
            raise ValueError("event_type is invalid.")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object.")
        payment_id = payload.get("payment_attempt_id")
        if payment_id is not None and (
            isinstance(payment_id, bool)
            or not isinstance(payment_id, int)
            or payment_id <= 0
        ):
            raise ValueError("payment_attempt_id must be a positive integer.")
        status = payload.get("status")
        if payment_id is not None and status not in ("success", "failed"):
            raise ValueError("Unknown payment status.")
        provider_charge_id = payload.get("provider_charge_id")
        if payment_id is not None and (
            not isinstance(provider_charge_id, str)
            or not provider_charge_id
            or len(provider_charge_id) > 255
        ):
            raise ValueError("provider_charge_id is required.")

        if WebhookEvent.objects.filter(
            provider=provider, provider_event_id=provider_event_id
        ).exists():
            return
        try:
            with transaction.atomic():
                webhook = WebhookEvent.objects.create(
                    provider=provider,
                    provider_event_id=provider_event_id,
                    event_type=event_type,
                    payload=payload,
                )
        except IntegrityError:
            return

        try:
            payment = PaymentAttempt.objects.get(pk=payment_id)
        except (PaymentAttempt.DoesNotExist, ValueError, TypeError):
            webhook.processed = True
            webhook.save(update_fields=["processed"])
            return

        webhook.organization_id = payment.organization_id
        webhook.save(update_fields=["organization"])
        PaymentService.settle(
            payment,
            status == "success",
            payload,
            provider_charge_id=provider_charge_id or "",
        )
        webhook.processed = True
        webhook.save(update_fields=["processed"])
