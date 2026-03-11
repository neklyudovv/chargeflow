from dataclasses import dataclass

from infrastructure.events import DomainEvent


@dataclass
class OrganizationCreated(DomainEvent):
    organization_id: int = 0


@dataclass
class CustomerCreated(DomainEvent):
    customer_id: int = 0


@dataclass
class CustomerUpdated(DomainEvent):
    customer_id: int = 0


@dataclass
class ApiKeyCreated(DomainEvent):
    api_key_id: int = 0


@dataclass
class ApiKeyRevoked(DomainEvent):
    api_key_id: int = 0
