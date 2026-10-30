import hashlib
import json
import random
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction

from payments.application.gateway import ChargeRequest, ChargeResult
from payments.domain.models import MockChargeReceipt


class MockPaymentGateway:
    def charge(self, request: ChargeRequest) -> ChargeResult:
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "amount": str(Decimal(request.amount).quantize(Decimal("0.01"))),
                    "currency": request.currency,
                    "provider_customer_id": request.provider_customer_id,
                    "provider_payment_method_id": request.provider_payment_method_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        receipt = MockChargeReceipt.objects.filter(
            provider_idempotency_key=request.idempotency_key
        ).first()
        if receipt is None:
            success = random.random() >= settings.PAYMENT_FAILURE_RATE
            response = {"status": "ok" if success else "declined"}
            try:
                with transaction.atomic():
                    receipt = MockChargeReceipt.objects.create(
                        provider_idempotency_key=request.idempotency_key,
                        request_fingerprint=request_fingerprint,
                        success=success,
                        provider_charge_id=f"mock_{uuid.uuid4().hex}",
                        provider_response=response,
                    )
            except IntegrityError:
                receipt = MockChargeReceipt.objects.get(
                    provider_idempotency_key=request.idempotency_key
                )

        if receipt.request_fingerprint != request_fingerprint:
            raise ValueError("Provider idempotency key was reused with different parameters.")
        return ChargeResult(
            receipt.success,
            receipt.provider_charge_id,
            receipt.provider_response,
        )
