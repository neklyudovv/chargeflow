from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.models import Customer
from accounts.request_context import get_request_organization
from plans.models import Plan
from subscriptions.api.serializers import SubscriptionCreateSerializer, SubscriptionSerializer
from subscriptions.application.services import SubscriptionService
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

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Subscription.objects.none()
        return Subscription.objects.select_related("customer", "plan").filter(
            customer__organization=org
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
            subscription = SubscriptionService.create(customer, plan)
        except ValueError as exc:
            # e.g. subscribing to an archived plan - a bad request, not a fault
            raise ValidationError({"detail": str(exc)})
        return Response(SubscriptionSerializer(subscription).data, status=status.HTTP_201_CREATED)

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
