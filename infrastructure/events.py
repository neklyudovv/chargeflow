from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from logging import getLogger

from django.db import transaction

logger = getLogger(__name__)


@dataclass
class DomainEvent:
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CeleryEventDispatcher:
    """Routes domain events to Celery tasks.
    """

    def __init__(self):
        self._subscribers = defaultdict(list)

    def subscribe(self, event_type, task) -> None:
        self._subscribers[event_type].append(task)

    def publish(self, event: DomainEvent) -> None:
        tasks = self._subscribers.get(type(event), [])
        logger.info(f"Event published: {event}")
        if not tasks:
            return
        payload = {k: v for k, v in asdict(event).items() if k != "occurred_at"}
        transaction.on_commit(lambda: self._enqueue(event, tasks, payload))

    def _enqueue(self, event, tasks, payload: dict) -> None:
        for task in tasks:
            try:
                task.delay(**payload)
            except Exception:
                logger.exception(f"Failed to enqueue {task} for event {event}")


event_dispatcher = CeleryEventDispatcher()
