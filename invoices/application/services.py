from django.db import transaction

from infrastructure.events import event_bus
from invoices.domain.events import InvoiceFailed, InvoiceIssued, InvoicePaid
from invoices.domain.models import Invoice, InvoiceLine, InvoiceStatus


class InvoiceService:
    @staticmethod
    @transaction.atomic
    def generate_for_subscription(subscription) -> Invoice:
        invoice = Invoice.objects.create(
            subscription=subscription,
            status=InvoiceStatus.DRAFT,
            total=subscription.plan.price,
            currency=subscription.plan.currency,
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description=f"{subscription.plan.name} ({subscription.current_period_start.date()} — {subscription.current_period_end.date()})",
            amount=subscription.plan.price,
        )
        invoice.transition_to(InvoiceStatus.ISSUED)
        event_bus.publish(InvoiceIssued(invoice_id=invoice.pk))
        return invoice

    @staticmethod
    @transaction.atomic
    def mark_paid(invoice: Invoice) -> None:
        invoice.transition_to(InvoiceStatus.PAID)
        event_bus.publish(InvoicePaid(invoice_id=invoice.pk))

    @staticmethod
    @transaction.atomic
    def mark_failed(invoice: Invoice) -> None:
        invoice.transition_to(InvoiceStatus.FAILED)
        event_bus.publish(InvoiceFailed(invoice_id=invoice.pk))
