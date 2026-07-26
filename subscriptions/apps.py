from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    name = "subscriptions"

    def ready(self):
        from infrastructure.events import event_dispatcher
        from invoices.domain.events import InvoiceFailed, InvoiceOverdue, InvoicePaid
        from subscriptions import tasks

        event_dispatcher.subscribe(
            InvoiceFailed, tasks.mark_subscription_overdue_for_invoice
        )
        event_dispatcher.subscribe(
            InvoicePaid, tasks.mark_subscription_active_for_invoice
        )
        event_dispatcher.subscribe(
            InvoiceOverdue, tasks.close_subscription_for_non_payment
        )
