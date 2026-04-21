from invoices.domain.models import Invoice
from payments.application.services import PaymentService


def on_invoice_issued(event) -> None:
    invoice = Invoice.objects.get(pk=event.invoice_id)
    PaymentService.attempt(invoice)
