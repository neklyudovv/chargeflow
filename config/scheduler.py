from pathlib import Path

from celery.beat import PersistentScheduler


class HealthcheckedScheduler(PersistentScheduler):
    def tick(self, *args, **kwargs):
        delay = super().tick(*args, **kwargs)
        Path('/app/run/beat-heartbeat').touch()
        return min(delay, 30)
