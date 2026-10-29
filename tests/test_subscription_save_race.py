from concurrent.futures import ThreadPoolExecutor
from threading import Event, local
from time import monotonic, sleep

import pytest
from django.db import close_old_connections, connection

from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription


@pytest.mark.django_db(transaction=True)
def test_ordinary_save_and_activation_share_lock_order(subscription, monkeypatch):
    saved, release, activating = Event(), Event(), Event()
    state = local()
    activation_pid = []
    original_clean = Subscription.clean

    def paused_clean(instance):
        original_clean(instance)
        if getattr(state, 'ordinary', False):
            saved.set()
            assert release.wait(10)

    monkeypatch.setattr(Subscription, 'clean', paused_clean)

    def ordinary_save():
        close_old_connections()
        try:
            state.ordinary = True
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
            row = Subscription.objects.get(pk=subscription.pk)
            row.save(update_fields=['updated_at'])
            return 'saved'
        except Exception as exc:
            return type(exc).__name__ + ': ' + str(exc)
        finally:
            close_old_connections()

    def activate():
        close_old_connections()
        try:
            assert saved.wait(10)
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute('SELECT pg_backend_pid()')
                activation_pid.append(cursor.fetchone()[0])
            activating.set()
            SubscriptionService.activate(subscription)
            return 'activated'
        except Exception as exc:
            return type(exc).__name__ + ': ' + str(exc)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        saver, activator = pool.submit(ordinary_save), pool.submit(activate)
        try:
            assert activating.wait(10)
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT cardinality(pg_blocking_pids(%s)) > 0', [activation_pid[0]])
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                sleep(0.01)
            assert blocked
        finally:
            release.set()
        results = [saver.result(12), activator.result(12)]
    assert results == ['saved', 'activated'], results
