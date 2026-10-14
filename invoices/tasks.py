from datetime import timedelta
from logging import getLogger

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from payments.domain.models import PaymentStatus
from payments.tasks import charge_invoice
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import (
    CancellationReason,
    Subscription,
    SubscriptionStatus,
)

# db errors retry with backoff
# logic errors are not listed here, so they surface instead of looping forever
RETRY = {"autoretry_for": (OperationalError,), "max_retries": 5, "retry_backoff": True}

logger = getLogger(__name__)

MAX_DUNNING_ATTEMPTS = 3
STUCK_CHARGE_AFTER = timedelta(minutes=10)


@shared_task(**RETRY)
def run_dunning():
    # reissuing a failed invoice hands its charge to the worker again
    failed_ids = list(
        Invoice.objects.filter(status=InvoiceStatus.FAILED).values_list("pk", flat=True)
    )
    # One bad invoice must not hold up the rest of the batch; the cycle is
    # hourly and every step rechecks its own preconditions, so a skipped
    # failure is only deferred to the next cycle.
    for invoice_id in failed_ids:
        try:
            _dun_invoice(invoice_id)
        except (OperationalError, SoftTimeLimitExceeded):
            # transient db trouble - let the task-level retry take the batch
            raise
        except Exception:
            logger.exception("Dunning failed for invoice %s", invoice_id)


@transaction.atomic
def _dun_invoice(invoice_id):
    subscription_id = Invoice.objects.values_list("subscription_id", flat=True).get(pk=invoice_id)
    # lock order is subscription -> invoice -> attempt everywhere
    subscription = Subscription.objects.select_for_update().get(pk=subscription_id)
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    # a payment or a cancel won while the batch was running
    if invoice.status != InvoiceStatus.FAILED:
        return
    failed_charges = invoice.payment_attempts.filter(
        status=PaymentStatus.FAILED
    ).count()
    if failed_charges >= MAX_DUNNING_ATTEMPTS:
        InvoiceService.mark_overdue(invoice)
        if subscription.status != SubscriptionStatus.CANCELED:
            SubscriptionService.cancel(subscription, reason=CancellationReason.NON_PAYMENT)
        return
    InvoiceService.reissue_for_retry(invoice)


@shared_task(**RETRY)
def reconcile_stuck():
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
