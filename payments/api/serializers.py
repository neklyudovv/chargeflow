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


class WebhookPayloadSerializer(serializers.Serializer):
    payment_attempt_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=["success", "failed"])
    provider_charge_id = serializers.CharField(max_length=255, allow_blank=False)

    def to_internal_value(self, data):
        expected = {"payment_attempt_id", "status", "provider_charge_id"}
        if not isinstance(data, dict) or set(data) != expected:
            raise serializers.ValidationError(
                "payload must contain payment_attempt_id, status and provider_charge_id."
            )
        return super().to_internal_value(data)


class WebhookSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["mock"])
    provider_event_id = serializers.CharField(max_length=255)
    event_type = serializers.CharField(max_length=255)
    payload = WebhookPayloadSerializer()

    def to_internal_value(self, data):
        expected = {"provider", "provider_event_id", "event_type", "payload"}
        if not isinstance(data, dict) or set(data) != expected:
            raise serializers.ValidationError("Unknown or missing webhook fields.")
        return super().to_internal_value(data)


class WebhookResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])
