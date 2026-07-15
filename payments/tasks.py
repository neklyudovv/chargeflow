from celery import shared_task
from django.db import OperationalError

from invoices.domain.models import Invoice
from payments.application.services import InvoiceNotPayable, PaymentService

# db errors, logic errors surface instead of looping.
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}


@shared_task(**RETRY)
def attempt_payment_for_invoice(invoice_id):
    invoice = Invoice.objects.get(pk=invoice_id)
    try:
        PaymentService.attempt(invoice)
    except InvoiceNotPayable:
        # invoice already left in a payable state (paid elsewhere or a re-delivered
        # event) -nothing to do
        return
