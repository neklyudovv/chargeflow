import pytest
from django.utils import timezone

from invoices.application.services import InvoiceService
from invoices.domain.models import Invoice, InvoiceStatus
from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus, WebhookEvent
from payments.tasks import charge_invoice
from subscriptions.domain.models import Subscription, SubscriptionStatus

pytestmark = pytest.mark.django_db(transaction=True)


def test_delayed_failure_of_old_attempt_cannot_abort_queued_retry(subscription, invoice, monkeypatch):
    Subscription.objects.filter(pk=subscription.pk).update(status=SubscriptionStatus.ACTIVE)
    Invoice.objects.filter(pk=invoice.pk).update(status=InvoiceStatus.ISSUED, issued_at=timezone.now())
    old = PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total)
    PaymentService.settle(old, False, {'provider_charge_id': 'charge_old_failure'})
    queued = []
    monkeypatch.setattr(charge_invoice, 'delay', queued.append)
    InvoiceService.reissue_for_retry(invoice)
    assert queued == [invoice.pk]

    PaymentService.handle_webhook('payment.updated', {'payment_attempt_id': old.pk, 'status': 'failed', 'provider_charge_id': 'charge_old_failure'}, provider='mock', provider_event_id='evt_delayed_failure')
    charge_invoice(queued[0])

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.payment_attempts.filter(status=PaymentStatus.SUCCESS).count() == 1
    assert invoice.payment_attempts.count() == 2


@pytest.mark.parametrize('shape', ['missing', 'extra', 'empty', 'array'])
def test_signed_webhook_with_wrong_top_level_fields_returns_400(settings, shape):
    import hashlib
    import hmac
    import json
    import time
    from rest_framework.test import APIClient

    data = {'provider': 'mock', 'provider_event_id': 'evt_wrong_shape', 'event_type': 'payment.updated', 'payload': {'payment_attempt_id': 999999, 'status': 'failed', 'provider_charge_id': 'charge_wrong_shape'}}
    if shape == 'empty':
        data = {}
    elif shape == 'array':
        data = []
    elif shape == 'missing':
        data.pop('provider_event_id')
    else:
        data['unexpected'] = 'value'
    body = json.dumps(data).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(settings.WEBHOOK_SECRET.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    client = APIClient(raise_request_exception=False)
    response = client.post('/api/payments/webhook/', body, content_type='application/json', HTTP_X_WEBHOOK_SIGNATURE=f't={timestamp},v1={digest}')
    assert response.status_code == 400
    assert not WebhookEvent.objects.exists()
