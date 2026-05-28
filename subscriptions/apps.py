from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    name = "subscriptions"

    def ready(self):
        from infrastructure.events import event_bus
        from invoices.domain.events import InvoiceFailed
        from subscriptions import tasks

        event_bus.subscribe(
            InvoiceFailed,
            lambda e: tasks.mark_subscription_overdue_for_invoice.delay(e.invoice_id),
        )
