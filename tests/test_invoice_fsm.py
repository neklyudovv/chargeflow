import pytest

from invoices.domain.models import (
    INVOICE_TRANSITIONS,
    InvoiceStatus,
)
from subscriptions.domain.models import InvalidStatusTransition

ALL_STATUSES = set(InvoiceStatus.values)

LEGAL_EDGES = [
    (src, dst)
    for src, targets in INVOICE_TRANSITIONS.items()
    for dst in targets
]

ILLEGAL_EDGES = [
    (src, dst)
    for src in ALL_STATUSES
    for dst in ALL_STATUSES
    if src != dst and dst not in INVOICE_TRANSITIONS.get(src, set())
]

TERMINAL_STATUSES = [s for s, targets in INVOICE_TRANSITIONS.items() if not targets]


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


def test_failed_can_retry_to_issued(invoice):
    _force_status(invoice, InvoiceStatus.FAILED)

    invoice.transition_to(InvoiceStatus.ISSUED)

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.ISSUED


@pytest.mark.parametrize("terminal", TERMINAL_STATUSES)
def test_terminal_statuses_have_no_exits(invoice, terminal):
    _force_status(invoice, terminal)

    for dst in ALL_STATUSES - {terminal}:
        with pytest.raises(InvalidStatusTransition):
            invoice.transition_to(dst)
