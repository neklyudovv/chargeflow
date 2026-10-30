from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ChargeRequest:
    amount: Decimal
    currency: str
    provider_customer_id: str
    provider_payment_method_id: str
    idempotency_key: str


@dataclass(frozen=True)
class ChargeResult:
    success: bool
    provider_charge_id: str
    provider_response: dict


class PaymentGateway(Protocol):
    def charge(self, request: ChargeRequest) -> ChargeResult:
        ...
