from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    name = "subscriptions"

    def ready(self):
        from infrastructure.events import event_bus
        from invoices.domain.events import InvoiceFailed
        from subscriptions.handlers import on_invoice_failed

        event_bus.subscribe(InvoiceFailed, on_invoice_failed)
