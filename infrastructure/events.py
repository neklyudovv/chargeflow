from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from logging import getLogger

from django.db import transaction

logger = getLogger(__name__)


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
        logger.info(f"Event published: {event}")
        if not handlers:
            return
        transaction.on_commit(lambda: self._dispatch(event, handlers))

    def _dispatch(self, event: DomainEvent, handlers: list[Callable]) -> None:
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(f"Event handler {handler} failed for event {event}")


event_bus = EventBus()
