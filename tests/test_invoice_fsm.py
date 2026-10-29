import pytest

from invoices.domain.models import InvoiceStatus
from subscriptions.domain.models import InvalidStatusTransition

ALL_STATUSES = set(InvoiceStatus.values)

EXPECTED_TRANSITIONS = {
    InvoiceStatus.DRAFT: {InvoiceStatus.ISSUED, InvoiceStatus.CANCELED},
    InvoiceStatus.ISSUED: {
        InvoiceStatus.PAID,
        InvoiceStatus.FAILED,
        InvoiceStatus.OVERDUE,
        InvoiceStatus.CANCELED,
    },
    InvoiceStatus.FAILED: {
        InvoiceStatus.ISSUED,
        InvoiceStatus.PAID,
        InvoiceStatus.OVERDUE,
        InvoiceStatus.CANCELED,
    },
    InvoiceStatus.OVERDUE: {InvoiceStatus.PAID, InvoiceStatus.CANCELED},
    InvoiceStatus.PAID: set(),
    InvoiceStatus.CANCELED: set(),
}

LEGAL_EDGES = [
    (src, dst)
    for src, targets in EXPECTED_TRANSITIONS.items()
    for dst in targets
]

ILLEGAL_EDGES = [
    (src, dst)
    for src in ALL_STATUSES
    for dst in ALL_STATUSES
    if src != dst and dst not in EXPECTED_TRANSITIONS.get(src, set())
]


def _force_status(invoice, status):
    invoice.status = status
    invoice.save(update_fields=["status"])


@pytest.mark.parametrize("src,dst", LEGAL_EDGES)
def test_legal_transition_succeeds(invoice, src, dst):
    _force_status(invoice, src)

    invoice.transition_to(dst)

    invoice.refresh_from_db()
    assert invoice.status == dst


@pytest.mark.parametrize("src,dst", ILLEGAL_EDGES)
def test_illegal_transition_raises(invoice, src, dst):
    _force_status(invoice, src)

    with pytest.raises(InvalidStatusTransition):
        invoice.transition_to(dst)

    invoice.refresh_from_db()
    assert invoice.status == src


def test_issued_sets_issued_at(invoice):
    assert invoice.issued_at is None

    invoice.transition_to(InvoiceStatus.ISSUED)

    invoice.refresh_from_db()
    assert invoice.issued_at is not None


def test_paid_sets_paid_at(invoice):
    _force_status(invoice, InvoiceStatus.ISSUED)
    assert invoice.paid_at is None

    invoice.transition_to(InvoiceStatus.PAID)

    invoice.refresh_from_db()
    assert invoice.paid_at is not None
