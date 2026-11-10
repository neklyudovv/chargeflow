from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from invoices.domain.models import Invoice, InvoiceLine, InvoiceStatus
from subscriptions.domain.models import CancellationReason, Subscription, SubscriptionStatus

RETRY_DAYS = (1, 3, 7)


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
        invoice.next_retry_at = None
        invoice.save(update_fields=["next_retry_at", "updated_at"])

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
    def record_payment_failure(invoice: Invoice) -> None:
        from subscriptions.application.services import SubscriptionService

        subscription = Subscription.objects.select_for_update().get(pk=invoice.subscription_id)
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status not in [InvoiceStatus.ISSUED, InvoiceStatus.FAILED]:
            return
        InvoiceService.mark_failed(invoice)
        invoice.refresh_from_db()
        invoice.first_failed_at = invoice.first_failed_at or timezone.now()
        failed_count = invoice.payment_attempts.filter(status="failed").count()
        if failed_count > len(RETRY_DAYS):
            invoice.next_retry_at = None
            InvoiceService.mark_overdue(invoice)
            SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)
        else:
            invoice.next_retry_at = invoice.first_failed_at + timedelta(days=RETRY_DAYS[failed_count - 1])
            SubscriptionService.mark_overdue(subscription)
        invoice.save(update_fields=["first_failed_at", "next_retry_at", "updated_at"])

    @staticmethod
    @transaction.atomic
    def retry_due(invoice_id: int) -> None:
        subscription_id = Invoice.objects.values_list("subscription_id", flat=True).get(pk=invoice_id)
        # skip a busy subscription before locking its invoice to preserve settlement lock order
        subscription = Subscription.objects.select_for_update(skip_locked=True).filter(pk=subscription_id).first()
        if subscription is None or subscription.status == SubscriptionStatus.CANCELED:
            return
        invoice = Invoice.objects.select_for_update(skip_locked=True).filter(
            pk=invoice_id, status=InvoiceStatus.FAILED, next_retry_at__lte=timezone.now()
        ).first()
        if invoice is None:
            return
        if invoice.payment_attempts.filter(status="failed").count() > len(RETRY_DAYS):
            from subscriptions.application.services import SubscriptionService

            InvoiceService.mark_overdue(invoice)
            invoice.next_retry_at = None
            invoice.save(update_fields=["next_retry_at", "updated_at"])
            SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)
            return
        InvoiceService.reissue_for_retry(invoice)

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
            invoice.next_retry_at = None
            invoice.save(update_fields=["next_retry_at", "updated_at"])

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
        invoice.next_retry_at = None
        invoice.save(update_fields=["next_retry_at", "updated_at"])
        from payments.tasks import charge_invoice
        transaction.on_commit(lambda: charge_invoice.delay(invoice.pk))
