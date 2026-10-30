from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from django.db import OperationalError, close_old_connections, connection, transaction
from django.utils import timezone

from accounts.models import Customer
from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus, WebhookEvent
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription, SubscriptionStatus

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def payable(subscription, invoice):
    Subscription.objects.filter(pk=subscription.pk).update(status=SubscriptionStatus.ACTIVE)
    Invoice.objects.filter(pk=invoice.pk).update(status=InvoiceStatus.ISSUED, issued_at=timezone.now())
    return invoice


def test_gateway_sees_committed_pending_without_billing_transaction(payable, monkeypatch):
    import payments.application.services as services
    from payments.application.gateway import ChargeResult

    def charge(request):
        assert not connection.in_atomic_block
        with ThreadPoolExecutor(max_workers=1) as pool:
            def read_committed():
                close_old_connections()
                try:
                    return PaymentAttempt.objects.get(invoice=payable)
                finally:
                    close_old_connections()
            pending = pool.submit(read_committed).result(timeout=10)
        assert pending.status == PaymentStatus.PENDING
        assert request.idempotency_key == pending.provider_idempotency_key
        assert request.amount == Decimal('19.99')
        return ChargeResult(True, 'charge_committed', {'status': 'ok'})

    monkeypatch.setattr(services.get_payment_gateway('mock'), 'charge', charge)
    payment = PaymentService.attempt(payable, 'client-key')
    assert payment.status == PaymentStatus.SUCCESS
    assert payment.provider_charge_id == 'charge_committed'
    assert payment.provider_idempotency_key != 'client-key'


@pytest.mark.parametrize('entrypoint', ['attempt', 'charge_issued'])
def test_charge_entrypoints_reject_outer_atomic_before_creating_attempt(payable, entrypoint):
    with transaction.atomic():
        with pytest.raises(RuntimeError, match='transaction|atomic'):
            if entrypoint == 'attempt':
                PaymentService.attempt(payable)
            else:
                PaymentService.charge_issued(payable.pk)
    assert not PaymentAttempt.objects.exists()


@pytest.mark.parametrize('crash', ['transport', 'settle'])
def test_retry_reuses_committed_attempt_and_immutable_gateway_parameters(payable, customer, monkeypatch, crash):
    import payments.application.services as services
    from payments.application.gateway import ChargeResult

    Customer.objects.filter(pk=customer.pk).update(provider_customer_id='cus_original', provider_payment_method_id='pm_original')
    requests = []
    original_settle = PaymentService.settle

    def charge(request):
        assert not connection.in_atomic_block
        requests.append(request)
        if crash == 'transport' and len(requests) == 1:
            raise ConnectionError('response lost')
        return ChargeResult(True, 'charge_replay', {'status': 'ok'})

    monkeypatch.setattr(services.get_payment_gateway('mock'), 'charge', charge)
    if crash == 'settle':
        def rollback(*args, **kwargs):
            with transaction.atomic():
                original_settle(*args, **kwargs)
                raise OperationalError('settle rolled back')
        monkeypatch.setattr(PaymentService, 'settle', rollback)
    with pytest.raises((ConnectionError, OperationalError)):
        PaymentService.attempt(payable, 'retry-key')
    pending = PaymentAttempt.objects.get(invoice=payable)
    assert pending.status == PaymentStatus.PENDING
    Customer.objects.filter(pk=customer.pk).update(provider_customer_id='cus_changed', provider_payment_method_id='pm_changed')
    Invoice.objects.filter(pk=payable.pk).update(total='99.00', currency='EUR')
    monkeypatch.setattr(PaymentService, 'settle', original_settle)
    recovered = PaymentService.attempt(payable, 'retry-key')
    assert recovered.pk == pending.pk
    assert recovered.status == PaymentStatus.SUCCESS
    assert requests[0] == requests[1]
    assert requests[1].provider_customer_id == 'cus_original'
    assert requests[1].provider_payment_method_id == 'pm_original'
    assert requests[1].currency == 'USD'


def test_mock_gateway_replays_across_processes_and_settings_changes(payable, settings):
    import json
    import os
    import subprocess
    import sys
    from dataclasses import asdict
    from payments.application.gateway import ChargeRequest
    from payments.infrastructure.mock_gateway import MockPaymentGateway

    request = ChargeRequest(Decimal('19.99'), 'USD', 'cus_mock', 'pm_mock', 'durable-replay')
    settings.PAYMENT_FAILURE_RATE = 0
    first = MockPaymentGateway().charge(request)
    script = """
import django, json
from decimal import Decimal
django.setup()
from django.conf import settings
from payments.application.gateway import ChargeRequest
from payments.infrastructure.mock_gateway import MockPaymentGateway
from dataclasses import asdict
settings.DATABASES['default']['NAME'] = __import__('os').environ['GATEWAY_TEST_DATABASE']
settings.PAYMENT_FAILURE_RATE = 1
request = ChargeRequest(Decimal('19.99'), 'USD', 'cus_mock', 'pm_mock', 'durable-replay')
print(json.dumps(asdict(MockPaymentGateway().charge(request))))
"""
    env = dict(os.environ, GATEWAY_TEST_DATABASE=connection.settings_dict['NAME'], DJANGO_SETTINGS_MODULE='config.settings')
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(result.stdout) == asdict(first)
    with pytest.raises(ValueError):
        MockPaymentGateway().charge(ChargeRequest(Decimal('20.00'), 'USD', 'cus_mock', 'pm_mock', 'durable-replay'))


@pytest.mark.parametrize('canceled', [False, True])
def test_stuck_pending_is_recovered_with_the_same_provider_key(payable, monkeypatch, canceled):
    import payments.application.services as services
    from invoices.tasks import reconcile_stuck
    from payments.application.gateway import ChargeResult

    gateway = services.get_payment_gateway('mock')
    def lost(request):
        raise ConnectionError('lost before settlement')
    monkeypatch.setattr(gateway, 'charge', lost)
    with pytest.raises(ConnectionError):
        PaymentService.attempt(payable)
    pending = PaymentAttempt.objects.get()
    if canceled:
        SubscriptionService.cancel(payable.subscription)
    old = timezone.now() - timedelta(hours=1)
    PaymentAttempt.objects.filter(pk=pending.pk).update(attempted_at=old)
    Invoice.objects.filter(pk=payable.pk).update(issued_at=old)
    keys = []
    def recover(request):
        keys.append(request.idempotency_key)
        return ChargeResult(True, 'charge_recovered', {'status': 'ok'})
    monkeypatch.setattr(gateway, 'charge', recover)
    reconcile_stuck()
    pending.refresh_from_db()
    assert pending.status == (PaymentStatus.PENDING if canceled else PaymentStatus.SUCCESS)
    assert pending.needs_reconciliation == canceled
    assert keys == [pending.provider_idempotency_key]
    assert PaymentAttempt.objects.count() == 1


@pytest.mark.parametrize('winner', ['cancel', 'webhook'])
def test_response_settlement_rechecks_winner_during_gateway_call(payable, monkeypatch, winner):
    import payments.application.services as services
    from payments.application.gateway import ChargeResult

    def charge(request):
        payment = PaymentAttempt.objects.get()
        if winner == 'cancel':
            SubscriptionService.cancel(payable.subscription)
        else:
            PaymentService.handle_webhook('payment.updated', {'payment_attempt_id': payment.pk, 'status': 'success', 'provider_charge_id': 'charge_race'}, provider='mock', provider_event_id='evt_race')
        return ChargeResult(winner == 'cancel', 'charge_race', {'status': 'ok' if winner == 'cancel' else 'declined'})
    monkeypatch.setattr(services.get_payment_gateway('mock'), 'charge', charge)
    result = PaymentService.attempt(payable)
    payable.refresh_from_db()
    result.refresh_from_db()
    if winner == 'cancel':
        assert payable.status == InvoiceStatus.CANCELED
        assert result.needs_reconciliation
        assert result.status != PaymentStatus.SUCCESS
    else:
        assert payable.status == InvoiceStatus.PAID
        assert result.status == PaymentStatus.SUCCESS


@pytest.mark.parametrize('winner', ['cancel', 'another_success'])
def test_direct_settle_of_stale_attempt_flags_unapplied_success(payable, winner):
    stale = PaymentAttempt.objects.create(invoice=payable, amount=payable.total, status=PaymentStatus.FAILED)
    if winner == 'cancel':
        SubscriptionService.cancel(payable.subscription)
    else:
        applied = PaymentAttempt.objects.create(invoice=payable, amount=payable.total)
        PaymentService.settle(applied, True, {'status': 'ok'})
    PaymentService.settle(stale, True, {'status': 'ok', 'provider_charge_id': 'late_charge'})
    stale.refresh_from_db()
    assert stale.needs_reconciliation
    assert stale.status == PaymentStatus.FAILED
    assert PaymentAttempt.objects.filter(status=PaymentStatus.SUCCESS).count() == (winner == 'another_success')


def test_concurrent_same_invoice_and_key_reuses_one_attempt(payable):
    barrier = Barrier(2)
    def charge(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return PaymentService.attempt(payable, 'concurrent-key').pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(charge, range(2)))
    assert len(set(ids)) == 1
    assert PaymentAttempt.objects.count() == 1
    assert PaymentAttempt.objects.get().status == PaymentStatus.SUCCESS


def test_provider_event_dedup_is_unique_and_processing_rolls_back(payable, monkeypatch):
    pending = PaymentAttempt.objects.create(invoice=payable, amount=payable.total)
    payload = {'payment_attempt_id': pending.pk, 'status': 'success', 'provider_charge_id': 'charge_webhook'}
    original = PaymentService.settle
    def rollback(*args, **kwargs):
        original(*args, **kwargs)
        raise OperationalError('rollback webhook')
    monkeypatch.setattr(PaymentService, 'settle', rollback)
    with pytest.raises(OperationalError):
        PaymentService.handle_webhook('payment.updated', payload, provider='mock', provider_event_id='evt_unique')
    assert not WebhookEvent.objects.exists()
    pending.refresh_from_db()
    assert pending.status == PaymentStatus.PENDING
    monkeypatch.setattr(PaymentService, 'settle', original)
    PaymentService.handle_webhook('payment.updated', payload, provider='mock', provider_event_id='evt_unique')
    PaymentService.handle_webhook('payment.updated', dict(payload, status='failed'), provider='mock', provider_event_id='evt_unique')
    assert WebhookEvent.objects.filter(provider='mock', provider_event_id='evt_unique').count() == 1
    pending.refresh_from_db()
    assert pending.status == PaymentStatus.SUCCESS
