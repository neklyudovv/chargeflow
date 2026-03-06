import hashlib
import hmac
import os


class WebhookSignatureError(Exception):
    pass


def verify_webhook_signature(payload_bytes: bytes, signature: str) -> None:
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        raise WebhookSignatureError("WEBHOOK_SECRET is not configured")

    expected = hmac.new(
        secret.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise WebhookSignatureError("Invalid webhook signature")
