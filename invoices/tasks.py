from datetime import timedelta
from logging import getLogger

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import OperationalError
from django.db.models import F
from django.utils import timezone

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus
from payments.tasks import charge_invoice
# db errors retry with backoff
# logic errors are not listed here, so they surface instead of looping forever
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}

logger = getLogger(__name__)

STUCK_CHARGE_AFTER = timedelta(minutes=10)


@shared_task(**RETRY)
def run_dunning():
    # reissuing a failed invoice hands its charge to the worker again
    failed_ids = list(
        Invoice.objects.filter(status=InvoiceStatus.FAILED, next_retry_at__lte=timezone.now()).values_list("pk", flat=True)
    )
    # One bad invoice must not hold up the rest of the batch; the cycle is
    # hourly and every step rechecks its own preconditions, so a skipped
    # failure is only deferred to the next cycle.
    for invoice_id in failed_ids:
        try:
            InvoiceService.retry_due(invoice_id)
        except (OperationalError, SoftTimeLimitExceeded):
            # a db error is retried at task level, a soft time limit ends the batch
            raise
        except Exception:
            logger.exception("Dunning failed for invoice %s", invoice_id)


@shared_task(**RETRY)
def reconcile_stuck():
    pending_ids = list(
        PaymentAttempt.objects.filter(
            status=PaymentStatus.PENDING,
            needs_reconciliation=False,
            attempted_at__lte=timezone.now() - STUCK_CHARGE_AFTER,
        ).values_list("pk", flat=True)
    )
    for payment_id in pending_ids:
        try:
            PaymentService.recover_pending(payment_id)
        except (OperationalError, SoftTimeLimitExceeded):
            raise
        except Exception:
            logger.exception("Reconcile failed for payment attempt %s", payment_id)

    # a hand-off lost between commit and the broker leaves an ISSUED invoice nobody charges
    stuck_ids = list(
        Invoice.objects.filter(
            status=InvoiceStatus.ISSUED,
            issued_at__lte=timezone.now() - STUCK_CHARGE_AFTER,
        )
        .exclude(payment_attempts__attempted_at__gte=F("issued_at"))
        .values_list("pk", flat=True)
    )
    for invoice_id in stuck_ids:
        try:
            charge_invoice.delay(invoice_id)
        except (OperationalError, SoftTimeLimitExceeded):
            raise
        except Exception:
            logger.exception("Reconcile failed for invoice %s", invoice_id)
