import pytest

from invoices.domain.models import PAYABLE_INVOICE_STATUSES, InvoiceStatus
from payments.application.services import (
    InvoiceNotPayable,
    PaymentService,
    get_payment_gateway,
)
from payments.application.gateway import ChargeResult
from payments.domain.models import PaymentAttempt, PaymentStatus

NON_PAYABLE_STATUSES = sorted(set(InvoiceStatus.values) - PAYABLE_INVOICE_STATUSES)
pytestmark = pytest.mark.django_db(transaction=True)


def _force_status(invoice, status):
    invoice.status = status
    invoice.save(update_fields=["status"])


def test_attempt_charges_payable_invoice(invoice):
    _force_status(invoice, InvoiceStatus.ISSUED)

    payment = PaymentService.attempt(invoice)

    invoice.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
    assert payment.amount == invoice.total
    assert payment.provider_response == {"status": "ok"}


@pytest.mark.parametrize("status", NON_PAYABLE_STATUSES)
def test_attempt_rejects_non_payable_invoice(invoice, status):
    _force_status(invoice, status)

    with pytest.raises(InvoiceNotPayable):
        PaymentService.attempt(invoice)

    assert not PaymentAttempt.objects.filter(invoice=invoice).exists()


def test_attempt_records_decline_when_provider_fails(invoice, monkeypatch):
    monkeypatch.setattr(
        get_payment_gateway("mock"),
        "charge",
        lambda request: ChargeResult(False, "charge_declined", {"status": "declined"}),
    )
    _force_status(invoice, InvoiceStatus.ISSUED)

    payment = PaymentService.attempt(invoice)

    assert payment.status == PaymentStatus.FAILED
    assert payment.provider_response == {"status": "declined"}


def test_attempt_replays_by_idempotency_key(invoice, monkeypatch):
    # a decline leaves the invoice payable, so only the key keeps the retry from charging again
    monkeypatch.setattr(
        get_payment_gateway("mock"),
        "charge",
        lambda request: ChargeResult(False, "charge_declined", {"status": "declined"}),
    )
    _force_status(invoice, InvoiceStatus.ISSUED)

    first = PaymentService.attempt(invoice, idempotency_key="req-123")
    assert first.status == PaymentStatus.FAILED

    second = PaymentService.attempt(invoice, idempotency_key="req-123")

    assert second.pk == first.pk
    assert PaymentAttempt.objects.filter(invoice=invoice).count() == 1
