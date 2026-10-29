from celery import shared_task
from django.db import OperationalError

from payments.application.services import PaymentService

# db errors, logic errors surface instead of looping.
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}


@shared_task(**RETRY)
def charge_invoice(invoice_id):
    PaymentService.charge_issued(invoice_id)
