from dataclasses import dataclass

from infrastructure.events import DomainEvent


@dataclass
class InvoiceIssued(DomainEvent):
    invoice_id: int = 0


@dataclass
class InvoicePaid(DomainEvent):
    invoice_id: int = 0


@dataclass
class InvoiceFailed(DomainEvent):
    invoice_id: int = 0


@dataclass
class InvoiceOverdue(DomainEvent):
    invoice_id: int = 0
