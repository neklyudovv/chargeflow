import hashlib
import hmac
import time
from django.conf import settings


class WebhookSignatureError(Exception):
    pass


def verify_webhook_signature(payload_bytes: bytes, signature: str) -> None:
    secret = settings.WEBHOOK_SECRET
    if not secret:
        raise WebhookSignatureError("WEBHOOK_SECRET is not configured")

    parts = signature.split(",")
    if len(parts) != 2 or not parts[0].startswith("t=") or not parts[1].startswith("v1="):
        raise WebhookSignatureError("Invalid webhook signature")
    timestamp_text = parts[0][2:]
    digest = parts[1][3:]
    if (
        not timestamp_text.isascii()
        or not timestamp_text.isdigit()
        or len(digest) != hashlib.sha256().digest_size * 2
        or not digest.isascii()
        or any(character not in "0123456789abcdefABCDEF" for character in digest)
    ):
        raise WebhookSignatureError("Invalid webhook signature")
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise WebhookSignatureError("Invalid webhook signature") from exc
    if abs(int(time.time()) - timestamp) > 300:
        raise WebhookSignatureError("Webhook timestamp is outside the allowed window")

    expected = hmac.new(
        secret.encode(),
        timestamp_text.encode() + b"." + payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, digest):
        raise WebhookSignatureError("Invalid webhook signature")
