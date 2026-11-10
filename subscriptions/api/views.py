from django.core.exceptions import ValidationError as ModelValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.models import Customer
from accounts.permissions import ProjectAccess
from accounts.request_context import get_request_organization
from plans.models import Plan
from subscriptions.api.serializers import SubscriptionCreateSerializer, SubscriptionSerializer
from subscriptions.application.services import SubscriptionConflict, SubscriptionService
from subscriptions.domain.models import InvalidStatusTransition, Subscription


def _apply(service_call, subscription):
    # The services own the lifecycle rules; a rejected transition is the caller
    # asking for something illegal, not a server fault.
    try:
        service_call(subscription)
    except (ValueError, InvalidStatusTransition) as exc:
        raise ValidationError({"detail": str(exc)})


class SubscriptionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = SubscriptionSerializer
    permission_classes = [ProjectAccess]

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Subscription.objects.none()
        return Subscription.objects.select_related("customer", "plan").filter(
            organization=org
        )

    @extend_schema(
        request=SubscriptionCreateSerializer,
        responses={200: SubscriptionSerializer, 201: SubscriptionSerializer, 409: OpenApiTypes.OBJECT},
        parameters=[OpenApiParameter(
            name="Idempotency-Key", type=OpenApiTypes.STR, location=OpenApiParameter.HEADER,
            description="Project-scoped subscription request key (1 to 255 characters)",
        )],
    )
    def create(self, request, *args, **kwargs):
        org = get_request_organization(request)
        if org is None:
            return Response(
                {"detail": "No organization in scope."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = SubscriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = get_object_or_404(
            Customer,
            pk=serializer.validated_data["customer_id"],
            organization=org,
        )
        plan = get_object_or_404(
            Plan,
            pk=serializer.validated_data["plan_id"],
            organization=org,
        )
        try:
            subscription = SubscriptionService.create(customer, plan, idempotency_key=request.headers.get("Idempotency-Key"))
        except SubscriptionConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ModelValidationError as exc:
            raise ValidationError(exc.message_dict)
        except ValueError as exc:
            # e.g. subscribing to an archived plan - a bad request, not a fault
            raise ValidationError({"detail": str(exc)})
        return Response(SubscriptionSerializer(subscription).data, status=status.HTTP_200_OK if getattr(subscription, "_idempotent_replay", False) else status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        subscription = self.get_object()
        _apply(SubscriptionService.activate, subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        subscription = self.get_object()
        _apply(SubscriptionService.cancel, subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        subscription = self.get_object()
        _apply(SubscriptionService.renew, subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)
