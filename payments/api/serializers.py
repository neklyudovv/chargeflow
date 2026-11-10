from rest_framework import serializers

from accounts.request_context import get_request_organization
from config.api import Request, TimeFilters
from invoices.domain.models import Invoice
from payments.domain.models import PaymentAttempt, PaymentStatus


class PaymentAttemptResponse(serializers.ModelSerializer):
    invoice_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = PaymentAttempt
        fields = ["id", "invoice_id", "status", "amount", "currency", "provider", "provider_charge_id", "attempted_at", "needs_reconciliation"]
        read_only_fields = fields


class CreatePaymentAttemptRequest(Request):
    invoice_id = serializers.PrimaryKeyRelatedField(queryset=Invoice.objects.none(), source="invoice", pk_field=serializers.IntegerField(min_value=1))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        org = get_request_organization(request) if request is not None else None
        if org is not None:
            self.fields["invoice_id"].queryset = Invoice.objects.filter(organization=org)


class PaymentEventPayloadRequest(Request):
    payment_attempt_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=["success", "failed"])
    provider_charge_id = serializers.CharField(max_length=255, allow_blank=False)


class PaymentEventRequest(Request):
    provider = serializers.ChoiceField(choices=["mock"])
    provider_event_id = serializers.CharField(max_length=255)
    event_type = serializers.CharField(max_length=255)
    payload = PaymentEventPayloadRequest()

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {"provider", "provider_event_id", "event_type", "payload"}:
            raise serializers.ValidationError({"non_field_errors": ["Unknown or missing webhook fields."]})
        return super().to_internal_value(data)


class PaymentEventResponse(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])


class PaymentFilters(TimeFilters):
    status = serializers.ChoiceField(choices=PaymentStatus.choices, required=False)
    invoice_id = serializers.IntegerField(min_value=1, required=False)
    needs_reconciliation = serializers.BooleanField(required=False)
