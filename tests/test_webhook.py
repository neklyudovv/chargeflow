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


def test_duplicate_webhook_is_processed_once(payment_attempt):
    payload = {"payment_attempt_id": payment_attempt.pk, "status": "success"}

    PaymentService.handle_webhook("payment.updated", payload)
    PaymentService.handle_webhook("payment.updated", payload)

    # The redelivery short-circuits on the processed row, so nothing is stored twice.
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
