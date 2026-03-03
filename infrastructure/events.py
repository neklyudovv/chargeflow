from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from django.db import transaction


@dataclass
class DomainEvent:
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    def __init__(self):
        self._handlers = defaultdict(list)

    def subscribe(self, event_type, handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> None:
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return
        transaction.on_commit(lambda: self._dispatch(event, handlers))

    def _dispatch(self, event: DomainEvent, handlers: list[Callable]) -> None:
        for handler in handlers:
            handler(event)


event_bus = EventBus()
