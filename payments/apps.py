from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = 'payments'

    def ready(self):
        from infrastructure.events import event_bus
        from invoices.domain.events import InvoiceIssued
        from payments.handlers import on_invoice_issued
        event_bus.subscribe(InvoiceIssued, on_invoice_issued)
