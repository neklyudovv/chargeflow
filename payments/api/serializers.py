from rest_framework import serializers

from payments.domain.models import PaymentAttempt


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        fields = ["id", "invoice", "status", "amount", "provider_response", "attempted_at"]
        read_only_fields = ["id", "status", "provider_response", "attempted_at"]


class PaymentAttemptCreateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()


class WebhookSerializer(serializers.Serializer):
    event_type = serializers.CharField()
    payload = serializers.DictField()
