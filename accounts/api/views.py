from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.api.serializers import (
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    CustomerSerializer,
    OrganizationSerializer,
    RegisterSerializer,
)
from accounts.events import (
    ApiKeyCreated,
    ApiKeyRevoked,
    CustomerCreated,
    CustomerUpdated,
    OrganizationCreated,
)
from accounts.models import ApiKey, Customer, Organization
from infrastructure.events import event_bus


class RegisterView(APIView):
    """
    POST /api/auth/register/
    Creates an Organization and returns its first API key (shown only once).
    No authentication required.
    """
    authentication_classes: list[type[BaseAuthentication]] = []
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        organization = Organization.objects.create(name=serializer.validated_data["name"])
        api_key_instance, raw_key = ApiKey.create_for_organization(organization, name="Default")
        event_bus.publish(OrganizationCreated(organization_id=organization.pk))
        event_bus.publish(ApiKeyCreated(api_key_id=api_key_instance.pk))

        return Response(
            {
                "organization": OrganizationSerializer(organization).data,
                "api_key": {
                    "id": api_key_instance.id,
                    "name": api_key_instance.name,
                    "key": raw_key,
                    "note": "Save this key - it will not be shown again.",
                },
            },
            status=status.HTTP_201_CREATED,
        )


class OrganizationMeView(APIView):
    """
    GET /api/accounts/me/ - returns the authenticated organization.
    Requires authentication.
    """

    def get(self, request):
        return Response(OrganizationSerializer(request.user).data)


class ApiKeyViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/accounts/keys/ - list active keys (no hashes, just metadata)
    POST   /api/accounts/keys/ - create a new key (returns raw key once)
    DELETE /api/accounts/keys/{id}/ - revoke a key
    """
    serializer_class = ApiKeySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ApiKey.objects.filter(organization=self.request.user, revoked=False)

    def create(self, request, *args, **kwargs):
        serializer = ApiKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key_instance, raw_key = ApiKey.create_for_organization(
            request.user,
            name=serializer.validated_data["name"],
            expires_at=serializer.validated_data.get("expires_at"),
        )
        event_bus.publish(ApiKeyCreated(api_key_id=api_key_instance.pk))
        return Response(
            {
                **ApiKeySerializer(api_key_instance).data,
                "key": raw_key,
                "note": "Save this key - it will not be shown again.",
            },
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        api_key = self.get_object()
        api_key.revoked = True
        api_key.save(update_fields=["revoked"])
        event_bus.publish(ApiKeyRevoked(api_key_id=api_key.pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return Customer.objects.select_related("organization").filter(
            organization=self.request.user
        )

    def perform_create(self, serializer):
        customer = serializer.save(organization=self.request.user)
        event_bus.publish(CustomerCreated(customer_id=customer.pk))

    def perform_update(self, serializer):
        customer = serializer.save()
        event_bus.publish(CustomerUpdated(customer_id=customer.pk))
