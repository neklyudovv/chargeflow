import pytest

from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus, WebhookEvent


@pytest.fixture
def payment_attempt(invoice):
    invoice.status = "issued"
    invoice.save(update_fields=["status"])
    return PaymentAttempt.objects.create(
        invoice=invoice,
        amount=invoice.total,
        status=PaymentStatus.PENDING,
    )


def test_success_webhook_marks_payment_succeeded(payment_attempt):
    PaymentService.handle_webhook(
        "payment.updated",
        {"payment_attempt_id": payment_attempt.pk, "status": "success"},
    )

    payment_attempt.refresh_from_db()
    assert payment_attempt.status == PaymentStatus.SUCCESS


def test_failed_webhook_marks_payment_failed(payment_attempt):
    PaymentService.handle_webhook(
        "payment.updated",
        {"payment_attempt_id": payment_attempt.pk, "status": "failed"},
    )

    payment_attempt.refresh_from_db()
    assert payment_attempt.status == PaymentStatus.FAILED


def test_late_failed_webhook_does_not_unsucceed_payment(payment_attempt):
    # the payload differs, so dedup does not catch it
    PaymentService.handle_webhook(
        "payment.updated",
        {"payment_attempt_id": payment_attempt.pk, "status": "success"},
    )
    PaymentService.handle_webhook(
        "payment.updated",
        {"payment_attempt_id": payment_attempt.pk, "status": "failed"},
    )

    payment_attempt.refresh_from_db()
    assert payment_attempt.status == PaymentStatus.SUCCESS


def test_duplicate_webhook_is_processed_once(payment_attempt):
    payload = {"payment_attempt_id": payment_attempt.pk, "status": "success"}

    PaymentService.handle_webhook("payment.updated", payload)
    PaymentService.handle_webhook("payment.updated", payload)

    stored = WebhookEvent.objects.filter(event_type="payment.updated", payload=payload)
    assert stored.count() == 1

    payment_attempt.refresh_from_db()
    assert payment_attempt.status == PaymentStatus.SUCCESS


def test_webhook_without_payment_id_is_marked_processed(db):
    PaymentService.handle_webhook("ping", {"foo": "bar"})

    webhook = WebhookEvent.objects.get(event_type="ping")
    assert webhook.processed is True


def test_webhook_for_unknown_payment_is_marked_processed(db):
    PaymentService.handle_webhook(
        "payment.updated",
        {"payment_attempt_id": 999_999, "status": "success"},
    )

    webhook = WebhookEvent.objects.get(event_type="payment.updated")
    assert webhook.processed is True


@pytest.mark.parametrize('sign_with_settings', [True, False])
def test_http_webhook_uses_the_configured_secret(db, settings, monkeypatch, sign_with_settings):
    import hashlib
    import hmac
    import json

    from rest_framework.test import APIClient

    settings.WEBHOOK_SECRET = 'configured-webhook-secret'
    monkeypatch.setenv('WEBHOOK_SECRET', 'changed-after-startup')
    body = json.dumps({'event_type': 'ping', 'payload': {}}).encode()
    secret = settings.WEBHOOK_SECRET if sign_with_settings else 'changed-after-startup'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = APIClient().post(
        '/api/payments/webhook/', body, content_type='application/json',
        HTTP_X_WEBHOOK_SIGNATURE=signature,
    )
    assert response.status_code == (200 if sign_with_settings else 400)
    assert WebhookEvent.objects.count() == (1 if sign_with_settings else 0)
