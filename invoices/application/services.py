from django.db import transaction

from invoices.domain.models import Invoice, InvoiceLine, InvoiceStatus
from subscriptions.domain.models import Subscription, SubscriptionStatus


class InvoiceService:
    # every method re-reads its row under lock: dunning and webhooks race on the same rows

    @staticmethod
    @transaction.atomic
    def generate_for_subscription(subscription) -> Invoice | None:
        subscription = Subscription.objects.select_for_update(of=("self",)).select_related("plan").get(pk=subscription.pk)
        # only ACTIVE subscriptions are billed
        if subscription.status != SubscriptionStatus.ACTIVE:
            return None
        # One invoice per subscription, billing period. get_or_create
        # + db unique constraint make this safe to call more than once for the
        # same period (e.g. a retried task)
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
        # local import: payments.application.services imports this module
        from payments.tasks import charge_invoice
        transaction.on_commit(lambda: charge_invoice.delay(invoice.pk))
        return invoice

    @staticmethod
    @transaction.atomic
    def mark_paid(invoice: Invoice) -> None:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status == InvoiceStatus.PAID:
            return
        invoice.transition_to(InvoiceStatus.PAID)

    @staticmethod
    @transaction.atomic
    def mark_failed(invoice: Invoice) -> None:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        # An OVERDUE invoice is still payable, but dunning has already given up
        # on it: a further rejection records the attempt and leaves it OVERDUE.
        if invoice.status != InvoiceStatus.ISSUED:
            return
        invoice.transition_to(InvoiceStatus.FAILED)

    @staticmethod
    @transaction.atomic
    def cancel_open_for_subscription(subscription) -> None:
        # when a subscription is canceled, stop pursuing its still open invoices
        # so dunning no longer retries them.
        open_invoices = subscription.invoices.select_for_update().filter(
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
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != InvoiceStatus.FAILED:
            return
        invoice.transition_to(InvoiceStatus.OVERDUE)

    @staticmethod
    @transaction.atomic
    def reissue_for_retry(invoice: Invoice) -> None:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        # only FAILED invoices are reissued
        if invoice.status != InvoiceStatus.FAILED:
            return
        invoice.transition_to(InvoiceStatus.ISSUED)
        from payments.tasks import charge_invoice
        transaction.on_commit(lambda: charge_invoice.delay(invoice.pk))
