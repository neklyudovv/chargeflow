from rest_framework import serializers

from payments.domain.models import PaymentAttempt


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        fields = [
            "id",
            "invoice",
            "status",
            "amount",
            "currency",
            "provider",
            "provider_charge_id",
            "attempted_at",
            "needs_reconciliation",
        ]
        read_only_fields = ["id", "status", "provider_response", "attempted_at", "needs_reconciliation"]


class PaymentAttemptCreateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()


class WebhookSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["mock"])
    provider_event_id = serializers.CharField(max_length=255)
    event_type = serializers.CharField(max_length=255)
    payload = serializers.DictField()

    def validate_payload(self, value):
        expected = {"payment_attempt_id", "status", "provider_charge_id"}
        if set(value) != expected:
            raise serializers.ValidationError(
                "payload must contain payment_attempt_id, status and provider_charge_id."
            )
        payment_id = value["payment_attempt_id"]
        if isinstance(payment_id, bool) or not isinstance(payment_id, int) or payment_id <= 0:
            raise serializers.ValidationError("payment_attempt_id must be a positive integer.")
        if value["status"] not in {"success", "failed"}:
            raise serializers.ValidationError("status must be success or failed.")
        if (
            not isinstance(value["provider_charge_id"], str)
            or not value["provider_charge_id"]
            or len(value["provider_charge_id"]) > 255
        ):
            raise serializers.ValidationError("provider_charge_id is required.")
        return value


class WebhookResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])
