from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from invoices.domain.models import Invoice
from payments.api.serializers import (
    PaymentAttemptCreateSerializer,
    PaymentAttemptSerializer,
    WebhookSerializer,
)
from payments.application.services import PaymentService
from payments.domain.models import PaymentAttempt
from payments.infrastructure.signature import WebhookSignatureError, verify_webhook_signature


class PaymentAttemptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PaymentAttempt.objects.select_related("invoice").all()
    serializer_class = PaymentAttemptSerializer

    def create(self, request, *args, **kwargs):
        serializer = PaymentAttemptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = get_object_or_404(Invoice, pk=serializer.validated_data["invoice_id"])
        payment = PaymentService.attempt(invoice)
        return Response(PaymentAttemptSerializer(payment).data, status=status.HTTP_201_CREATED)


class WebhookView(APIView):
    def post(self, request):
        signature = request.headers.get("X-Webhook-Signature", "")
        try:
            verify_webhook_signature(request.body, signature)
        except WebhookSignatureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        PaymentService.handle_webhook(
            event_type=serializer.validated_data["event_type"],
            payload=serializer.validated_data["payload"],
        )
        return Response({"status": "ok"})
