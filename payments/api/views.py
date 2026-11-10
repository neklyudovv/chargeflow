from django.conf import settings
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiTypes, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import ProjectAccess
from accounts.request_context import get_request_organization
from config.api import APIError, ErrorResponse, get_idempotency_key
from payments.api.serializers import CreatePaymentAttemptRequest, PaymentAttemptResponse, PaymentEventRequest, PaymentEventResponse, PaymentFilters
from payments.application.services import IdempotencyConflict, InvoiceNotPayable, PaymentService
from payments.domain.models import PaymentAttempt
from payments.infrastructure.signature import WebhookSignatureError, verify_webhook_signature


@extend_schema(tags=["Payments"])
@extend_schema_view(
    list=extend_schema(operation_id="listPaymentAttempts", summary="List payment attempts", description="", responses={200: PaymentAttemptResponse(many=True)}),
    retrieve=extend_schema(operation_id="getPaymentAttempt", summary="Get a payment attempt", description="", responses={200: PaymentAttemptResponse}),
)
class PaymentAttemptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentAttemptResponse
    permission_classes = [ProjectAccess]
    organization_scoped = True
    timestamp_field = "attempted_at"
    filter_serializer_class = PaymentFilters

    def get_queryset(self):
        org = get_request_organization(self.request)
        return PaymentAttempt.objects.select_related("invoice").filter(organization=org) if org is not None else PaymentAttempt.objects.none()

    @extend_schema(
        operation_id="createPaymentAttempt", summary="Attempt an invoice payment",
        description="",
        request=CreatePaymentAttemptRequest, responses={201: PaymentAttemptResponse, 409: ErrorResponse},
        parameters=[OpenApiParameter(name="Idempotency-Key", type={"type": "string", "minLength": 1, "maxLength": 255}, location=OpenApiParameter.HEADER, description="")],
        examples=[OpenApiExample("Pay an invoice", value={"invoice_id": 1}, request_only=True)],
    )
    def create(self, request, *args, **kwargs):
        if get_request_organization(request) is None:
            raise APIError("organization_required", "Select an organization before attempting payment.")
        serializer = CreatePaymentAttemptRequest(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            payment = PaymentService.attempt(serializer.validated_data["invoice"], idempotency_key=get_idempotency_key(request))
        except IdempotencyConflict as exc:
            raise APIError(exc.code, str(exc), status_code=409)
        except InvoiceNotPayable as exc:
            raise APIError(exc.code, str(exc))
        return Response(PaymentAttemptResponse(payment).data, status=201)


class WebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(
        operation_id="receivePaymentEvent", tags=["Provider webhooks"], summary="Receive a signed payment event",
        description="",
        request=PaymentEventRequest, responses={200: PaymentEventResponse, 503: ErrorResponse}, auth=[],
        parameters=[OpenApiParameter(name="X-Webhook-Signature", type=OpenApiTypes.STR, location=OpenApiParameter.HEADER, required=True, description="")],
        examples=[OpenApiExample("Payment settled", value={"provider": "mock", "provider_event_id": "evt_example", "event_type": "payment.updated", "payload": {"payment_attempt_id": 1, "status": "success", "provider_charge_id": "charge_example"}}, request_only=True)],
    )
    def post(self, request):
        if not settings.WEBHOOK_SECRET:
            raise APIError("service_unavailable", "Payment event verification is temporarily unavailable.", status_code=503)
        try:
            verify_webhook_signature(request.body, request.headers.get("X-Webhook-Signature", ""))
        except WebhookSignatureError:
            raise APIError("invalid_webhook_signature", "Invalid or expired payment event signature.")
        serializer = PaymentEventRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        PaymentService.handle_webhook(**serializer.validated_data)
        return Response(PaymentEventResponse({"status": "ok"}).data)
