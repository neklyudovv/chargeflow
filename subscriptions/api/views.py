from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.models import Customer
from plans.models import Plan
from subscriptions.api.serializers import SubscriptionCreateSerializer, SubscriptionSerializer
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription


class SubscriptionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Subscription.objects.select_related("customer", "plan").all()
    serializer_class = SubscriptionSerializer

    def create(self, request, *args, **kwargs):
        serializer = SubscriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = get_object_or_404(Customer, pk=serializer.validated_data["customer_id"])
        plan = get_object_or_404(Plan, pk=serializer.validated_data["plan_id"])
        subscription = SubscriptionService.create(customer, plan)
        return Response(SubscriptionSerializer(subscription).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        subscription = self.get_object()
        SubscriptionService.activate(subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        subscription = self.get_object()
        SubscriptionService.cancel(subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        subscription = self.get_object()
        SubscriptionService.renew(subscription)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)
