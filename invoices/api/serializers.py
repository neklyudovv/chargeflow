from rest_framework import serializers

from invoices.domain.models import Invoice, InvoiceLine


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["id", "description", "amount"]
        read_only_fields = ["id"]


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "subscription",
            "status",
            "total",
            "currency",
            "period_start",
            "period_end",
            "issued_at",
            "paid_at",
            "created_at",
            "updated_at",
            "lines",
        ]
        read_only_fields = [
            "id",
            "status",
            "issued_at",
            "paid_at",
            "created_at",
            "updated_at",
            "lines",
        ]
