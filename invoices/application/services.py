from django.db import transaction

from infrastructure.events import event_dispatcher
from invoices.domain.events import (
    InvoiceFailed,
    InvoiceIssued,
    InvoiceOverdue,
    InvoicePaid,
)
from invoices.domain.models import Invoice, InvoiceLine, InvoiceStatus


class InvoiceService:
    @staticmethod
    @transaction.atomic
    def generate_for_subscription(subscription) -> Invoice:
        # One invoice per subscription, billing period. get_or_create
        # + db unique constraint make this safe to call more than once for the
        # same period (e.g. a retried task or a re-delivered event)
        invoice, created = Invoice.objects.get_or_create(
            subscription=subscription,
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
            defaults={
                "status": InvoiceStatus.DRAFT,
                "total": subscription.plan.price,
                "currency": subscription.plan.currency,
            },
        )
        if not created:
            return invoice
        InvoiceLine.objects.create(
            invoice=invoice,
            description=f"{subscription.plan.name} ({subscription.current_period_start.date()} - {subscription.current_period_end.date()})",
            amount=subscription.plan.price,
        )
        invoice.transition_to(InvoiceStatus.ISSUED)
        event_dispatcher.publish(InvoiceIssued(invoice_id=invoice.pk))
        return invoice

    @staticmethod
    @transaction.atomic
    def mark_paid(invoice: Invoice) -> None:
        if invoice.status == InvoiceStatus.PAID:
            return
        invoice.transition_to(InvoiceStatus.PAID)
        event_dispatcher.publish(InvoicePaid(invoice_id=invoice.pk))

    @staticmethod
    @transaction.atomic
    def mark_failed(invoice: Invoice) -> None:
        # An OVERDUE invoice is still payable, but dunning has already given up
        # on it: a further rejection records the attempt and leaves it OVERDUE.
        if invoice.status != InvoiceStatus.ISSUED:
            return
        invoice.transition_to(InvoiceStatus.FAILED)
        event_dispatcher.publish(InvoiceFailed(invoice_id=invoice.pk))

    @staticmethod
    @transaction.atomic
    def cancel_open_for_subscription(subscription) -> None:
        # when a subscription is canceled, stop pursuing its still open invoices
        # so dunning no longer retries them.
        open_invoices = subscription.invoices.filter(
            status__in=[
                InvoiceStatus.DRAFT,
                InvoiceStatus.ISSUED,
                InvoiceStatus.FAILED,
            ]
        )
        for invoice in open_invoices:
            invoice.transition_to(InvoiceStatus.CANCELED)

    @staticmethod
    @transaction.atomic
    def mark_overdue(invoice: Invoice) -> None:
        if invoice.status != InvoiceStatus.FAILED:
            return
        invoice.transition_to(InvoiceStatus.OVERDUE)
        event_dispatcher.publish(InvoiceOverdue(invoice_id=invoice.pk))

    @staticmethod
    @transaction.atomic
    def reissue_for_retry(invoice: Invoice) -> None:
        # move a failed invoice back to ISSUED and reemit the event so
        # the normal payment flow charges it again 
        # only FAILED invoices are reissued
        if invoice.status != InvoiceStatus.FAILED:
            return
        invoice.transition_to(InvoiceStatus.ISSUED)
        event_dispatcher.publish(InvoiceIssued(invoice_id=invoice.pk))
