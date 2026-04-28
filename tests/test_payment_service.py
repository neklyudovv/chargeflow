import pytest

from invoices.domain.models import PAYABLE_INVOICE_STATUSES, InvoiceStatus
from payments.application.services import (
    InvoiceNotPayable,
    MockPaymentProvider,
    PaymentService,
)
from payments.domain.models import PaymentAttempt, PaymentStatus

NON_PAYABLE_STATUSES = sorted(set(InvoiceStatus.values) - PAYABLE_INVOICE_STATUSES)


def _force_status(invoice, status):
    invoice.status = status
    invoice.save(update_fields=["status"])


def test_attempt_charges_payable_invoice(invoice):
    _force_status(invoice, InvoiceStatus.ISSUED)

    payment = PaymentService.attempt(invoice)

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
    monkeypatch.setattr(MockPaymentProvider, "charge", lambda amount, currency: False)
    _force_status(invoice, InvoiceStatus.ISSUED)

    payment = PaymentService.attempt(invoice)

    assert payment.status == PaymentStatus.FAILED
    assert payment.provider_response == {"status": "declined"}
