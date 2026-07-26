from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "payments"

    def ready(self):
        from infrastructure.events import event_dispatcher
        from invoices.domain.events import InvoiceIssued
        from payments import tasks

        event_dispatcher.subscribe(InvoiceIssued, tasks.attempt_payment_for_invoice)
