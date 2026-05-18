from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.request_context import get_request_organization
from invoices.domain.models import Invoice
from payments.api.serializers import (
    PaymentAttemptCreateSerializer,
    PaymentAttemptSerializer,
    WebhookSerializer,
)
from payments.application.services import InvoiceNotPayable, PaymentService
from payments.domain.models import PaymentAttempt
from payments.infrastructure.signature import WebhookSignatureError, verify_webhook_signature


class PaymentAttemptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PaymentAttemptSerializer

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return PaymentAttempt.objects.none()
        return PaymentAttempt.objects.select_related("invoice").filter(
            invoice__subscription__customer__organization=org
        )

    def create(self, request, *args, **kwargs):
        org = get_request_organization(request)
        if org is None:
            return Response(
                {"detail": "No organization in scope."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PaymentAttemptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = get_object_or_404(
            Invoice,
            pk=serializer.validated_data["invoice_id"],
            subscription__customer__organization=org,
        )
        try:
            payment = PaymentService.attempt(invoice)
        except InvoiceNotPayable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PaymentAttemptSerializer(payment).data, status=status.HTTP_201_CREATED)


class WebhookView(APIView):
    # Webhook is called by payment provider - no API key auth needed
    authentication_classes: list[type[BaseAuthentication]] = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=WebhookSerializer,
        responses={200: OpenApiResponse(description="Event acknowledged.")},
    )
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
