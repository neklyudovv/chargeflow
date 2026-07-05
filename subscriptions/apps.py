from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    name = "subscriptions"

    def ready(self):
        from infrastructure.events import event_bus
        from invoices.domain.events import InvoiceFailed, InvoiceOverdue, InvoicePaid
        from subscriptions import tasks

        event_bus.subscribe(
            InvoiceFailed,
            lambda e: tasks.mark_subscription_overdue_for_invoice.delay(e.invoice_id),
        )
        event_bus.subscribe(
            InvoicePaid,
            lambda e: tasks.mark_subscription_active_for_invoice.delay(e.invoice_id),
        )
        event_bus.subscribe(
            InvoiceOverdue,
            lambda e: tasks.close_subscription_for_non_payment.delay(e.invoice_id),
        )
