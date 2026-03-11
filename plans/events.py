from dataclasses import dataclass

from infrastructure.events import DomainEvent


@dataclass
class PlanCreated(DomainEvent):
    plan_id: int = 0


@dataclass
class PlanUpdated(DomainEvent):
    plan_id: int = 0


@dataclass
class PlanArchived(DomainEvent):
    plan_id: int = 0


@dataclass
class PlanDeleted(DomainEvent):
    plan_id: int = 0
