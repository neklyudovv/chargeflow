from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "payments"

    def ready(self):
        from infrastructure.events import event_bus
        from invoices.domain.events import InvoiceIssued
        from payments import tasks

        event_bus.subscribe(
            InvoiceIssued,
            lambda e: tasks.attempt_payment_for_invoice.delay(e.invoice_id),
        )
