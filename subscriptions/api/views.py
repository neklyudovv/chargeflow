from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import ProjectAccess
from accounts.request_context import get_request_organization
from config.api import APIError, ErrorResponse, get_idempotency_key
from subscriptions.api.serializers import CreateSubscriptionRequest, SubscriptionFilters, SubscriptionResponse
from subscriptions.application.services import SubscriptionConflict, SubscriptionService
from subscriptions.domain.models import InvalidStatusTransition, Subscription


def _apply(request, service_call, subscription):
    if request.data:
        raise APIError("invalid_request", "This operation does not accept a request body.")
    try:
        service_call(subscription)
    except (ValueError, InvalidStatusTransition) as exc:
        raise APIError(getattr(exc, "code", "invalid_request"), str(exc))


@extend_schema(tags=["Subscriptions"])
@extend_schema_view(
    list=extend_schema(operation_id="listSubscriptions", summary="List subscriptions", description="", responses={200: SubscriptionResponse(many=True)}),
    retrieve=extend_schema(operation_id="getSubscription", summary="Get a subscription", description="", responses={200: SubscriptionResponse}),
)
class SubscriptionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = SubscriptionResponse
    permission_classes = [ProjectAccess]
    organization_scoped = True
    filter_serializer_class = SubscriptionFilters

    def get_queryset(self):
        org = get_request_organization(self.request)
        return Subscription.objects.select_related("customer", "plan").filter(organization=org) if org is not None else Subscription.objects.none()

    @extend_schema(
        operation_id="createSubscription", summary="Create a subscription",
        description="",
        request=CreateSubscriptionRequest, responses={200: SubscriptionResponse, 201: SubscriptionResponse, 409: ErrorResponse},
        parameters=[OpenApiParameter(name="Idempotency-Key", type={"type": "string", "minLength": 1, "maxLength": 255}, location=OpenApiParameter.HEADER, description="")],
        examples=[OpenApiExample("Subscribe a customer", value={"customer_id": 1, "plan_id": 1}, request_only=True)],
    )
    def create(self, request, *args, **kwargs):
        if get_request_organization(request) is None:
            raise APIError("organization_required", "Select an organization before creating a subscription.")
        serializer = CreateSubscriptionRequest(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            subscription = SubscriptionService.create(**serializer.validated_data, idempotency_key=get_idempotency_key(request))
        except SubscriptionConflict as exc:
            raise APIError(exc.code, str(exc), status_code=409)
        except ValueError as exc:
            raise APIError(getattr(exc, "code", "invalid_request"), str(exc))
        return Response(SubscriptionResponse(subscription).data, status=200 if getattr(subscription, "_idempotent_replay", False) else 201)

    @extend_schema(
        operation_id="activateSubscription", summary="Activate a trial subscription",
        description="",
        request=None, responses={200: SubscriptionResponse},
    )
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        subscription = self.get_object()
        _apply(request, SubscriptionService.activate, subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionResponse(subscription).data)

    @extend_schema(
        operation_id="cancelSubscription", summary="Cancel a subscription",
        description="",
        request=None, responses={200: SubscriptionResponse},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        subscription = self.get_object()
        _apply(request, SubscriptionService.cancel, subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionResponse(subscription).data)

    @extend_schema(
        operation_id="renewSubscription", summary="Renew an expired subscription",
        description="",
        request=None, responses={200: SubscriptionResponse},
    )
    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        subscription = self.get_object()
        _apply(request, SubscriptionService.renew, subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionResponse(subscription).data)
