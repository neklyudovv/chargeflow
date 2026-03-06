from dataclasses import dataclass

from infrastructure.events import DomainEvent


@dataclass
class PaymentSucceeded(DomainEvent):
    payment_attempt_id: int = 0


@dataclass
class PaymentFailed(DomainEvent):
    payment_attempt_id: int = 0


@dataclass
class WebhookReceived(DomainEvent):
    webhook_event_id: int = 0
