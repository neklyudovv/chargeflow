from dataclasses import dataclass

from infrastructure.events import DomainEvent


@dataclass
class SubscriptionCreated(DomainEvent):
    subscription_id: int = 0


@dataclass
class SubscriptionActivated(DomainEvent):
    subscription_id: int = 0


@dataclass
class SubscriptionOverdue(DomainEvent):
    subscription_id: int = 0


@dataclass
class SubscriptionCanceled(DomainEvent):
    subscription_id: int = 0


@dataclass
class SubscriptionRenewed(DomainEvent):
    subscription_id: int = 0
