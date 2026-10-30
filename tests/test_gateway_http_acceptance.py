import hashlib
import hmac
import json
import time

import pytest
from rest_framework.test import APIClient

from payments.domain.models import PaymentAttempt, WebhookEvent

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('age,valid,expected', [(0, True, 200), (301, True, 400), (-301, True, 400), (0, False, 400)])
def test_http_webhook_binds_timestamp_and_body(invoice, settings, age, valid, expected):
    pending = PaymentAttempt.objects.create(invoice=invoice, amount=invoice.total)
    body = json.dumps({'provider': 'mock', 'provider_event_id': 'evt_http', 'event_type': 'payment.updated', 'payload': {'payment_attempt_id': pending.pk, 'status': 'failed', 'provider_charge_id': 'charge_http'}}).encode()
    timestamp = str(int(time.time()) - age)
    digest = hmac.new(settings.WEBHOOK_SECRET.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    if not valid:
        timestamp = str(int(timestamp) + 1)
    signature = f't={timestamp},v1={digest}'
    client = APIClient()
    response = client.post('/api/payments/webhook/', body, content_type='application/json', HTTP_X_WEBHOOK_SIGNATURE=signature)
    assert response.status_code == expected
    assert WebhookEvent.objects.count() == (expected == 200)
    if expected == 200:
        replay = client.post('/api/payments/webhook/', body, content_type='application/json', HTTP_X_WEBHOOK_SIGNATURE=signature)
        assert replay.status_code == 200
        assert WebhookEvent.objects.get().provider_event_id == 'evt_http'
