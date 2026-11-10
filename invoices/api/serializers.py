from rest_framework import serializers

from config.api import TimeFilters
from invoices.domain.models import Invoice, InvoiceLine, InvoiceStatus


class InvoiceLineResponse(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["id", "description", "amount"]
        read_only_fields = fields


class InvoiceResponse(serializers.ModelSerializer):
    subscription_id = serializers.IntegerField(read_only=True)
    lines = InvoiceLineResponse(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "subscription_id", "status", "total", "currency", "period_start", "period_end", "first_failed_at", "next_retry_at", "attempt_count", "issued_at", "paid_at", "created_at", "updated_at", "lines"]
        read_only_fields = fields


class InvoiceFilters(TimeFilters):
    status = serializers.ChoiceField(choices=InvoiceStatus.choices, required=False)
    subscription_id = serializers.IntegerField(min_value=1, required=False)
    customer_id = serializers.IntegerField(min_value=1, required=False)
    currency = serializers.RegexField(r"^[A-Z]{3}$", required=False)
