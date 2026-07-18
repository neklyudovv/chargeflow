from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.authentication import BaseAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.api.serializers import (
    AcceptInvitationSerializer,
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    CustomerSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    LoginSerializer,
    OrganizationCreateSerializer,
    OrganizationMembershipSerializer,
    OrganizationSerializer,
    RegisterSerializer,
)
from accounts.events import (
    ApiKeyCreated,
    ApiKeyRevoked,
    CustomerCreated,
    CustomerUpdated,
    MemberInvited,
    MemberJoined,
    OrganizationCreated,
)
from accounts.models import ApiKey, Customer, Invitation, Organization, OrganizationMembership, User
from accounts.permissions import IsOrgAdmin
from accounts.request_context import get_request_organization
from infrastructure.events import event_bus


class RegisterView(APIView):
    """
    POST /api/auth/register/
    Creates User (email/password) and returns DRF Token.
    """
    authentication_classes: list[type[BaseAuthentication]] = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=RegisterSerializer,
        responses={201: OpenApiResponse(description="Returns an auth token and the created user.")},
    )
    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
        )
        token, _ = Token.objects.get_or_create(user=user)

        return Response(
            {
                "token": token.key,
                "user": {"id": user.id, "email": user.email},
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """
    POST /api/auth/login/
    Body: email, password.
    Returns DRF Token - use header: Authorization: Token <key>
    """
    authentication_classes: list[type[BaseAuthentication]] = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=LoginSerializer,
        responses={200: OpenApiResponse(description="Returns an auth token.")},
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        password = serializer.validated_data["password"]

        user = authenticate(request, username=email, password=password)
        if user is None:
            raise AuthenticationFailed("Invalid email or password.")

        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key})


class MeView(APIView):
    """GET /api/accounts/me/ - user, resolved organization scope, and role."""

    @extend_schema(
        responses={200: OpenApiResponse(description="Current user, resolved organization, and role.")}
    )
    def get(self, request):
        try:
            organization = get_request_organization(request)
            membership = getattr(request, "membership", None)
        except Exception:
            organization = None
            membership = None
        return Response(
            {
                "user": {"id": request.user.id, "email": request.user.email},
                "organization": (
                    OrganizationSerializer(organization, context={"request": request}).data
                    if organization
                    else None
                ),
                "role": membership.role if membership else None,
            }
        )


class OrganizationViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Organization.objects.filter(
            memberships__user=self.request.user
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return OrganizationCreateSerializer
        return OrganizationSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save(owner=request.user)
        # Organization.owner tracks accountability (billing, legal).
        # Access is governed by membership — get_request_organization queries memberships,
        # not owner, so the creator must have a record here too.
        OrganizationMembership.objects.create(
            organization=organization,
            user=request.user,
            role=OrganizationMembership.Role.OWNER,
        )
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


class ApiKeyViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ApiKeySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return ApiKey.objects.none()
        return ApiKey.objects.filter(organization=org, revoked=False)

    def create(self, request, *args, **kwargs):
        org = get_request_organization(request)
        if org is None:
            return Response(
                {"detail": "No organization in scope."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ApiKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key_instance, raw_key = ApiKey.create_for_organization(
            org,
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
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Customer.objects.none()
        return Customer.objects.select_related("organization").filter(organization=org)

    def perform_create(self, serializer):
        org = get_request_organization(self.request)
        if org is None:
            raise ValidationError("No organization in scope.")
        customer = serializer.save(organization=org)
        event_bus.publish(CustomerCreated(customer_id=customer.pk))

    def perform_update(self, serializer):
        customer = serializer.save()
        event_bus.publish(CustomerUpdated(customer_id=customer.pk))


class MemberViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = OrganizationMembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return OrganizationMembership.objects.none()
        return OrganizationMembership.objects.select_related("user").filter(organization=org)


class InvitationViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get_serializer_class(self):
        if self.action == "create":
            return InvitationCreateSerializer
        return InvitationSerializer

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Invitation.objects.none()
        return Invitation.objects.filter(organization=org, accepted_at__isnull=True).order_by("-created_at")

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        org = get_request_organization(request)
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        role = serializer.validated_data["role"]

        if OrganizationMembership.objects.filter(
            organization=org, user__email__iexact=email
        ).exists():
            raise ValidationError({"email": "This user is already a member of this organization."})

        invitation = Invitation.create_for_organization(org, email, role)
        event_bus.publish(MemberInvited(invitation_id=invitation.pk))
        return Response(
            {
                **InvitationSerializer(invitation).data,
                "token": invitation.token,
                "note": "In production this token would be emailed. Send it to the invitee to accept.",
            },
            status=status.HTTP_201_CREATED,
        )


class AcceptInvitationView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=AcceptInvitationSerializer,
        responses={201: OrganizationMembershipSerializer},
    )
    @transaction.atomic
    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            invitation = Invitation.objects.select_related("organization").get(
                token=serializer.validated_data["token"]
            )
        except Invitation.DoesNotExist:
            raise ValidationError({"token": "Invalid invitation token."})

        if invitation.is_accepted:
            raise ValidationError({"token": "This invitation has already been accepted."})
        if invitation.is_expired:
            raise ValidationError({"token": "This invitation has expired."})
        if invitation.email.lower() != request.user.email.lower():
            raise ValidationError({"token": "This invitation was sent to a different email address."})
        if OrganizationMembership.objects.filter(
            organization=invitation.organization, user=request.user
        ).exists():
            raise ValidationError({"token": "You are already a member of this organization."})

        membership = OrganizationMembership.objects.create(
            organization=invitation.organization,
            user=request.user,
            role=invitation.role,
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])

        event_bus.publish(MemberJoined(membership_id=membership.pk))
        return Response(OrganizationMembershipSerializer(membership).data, status=status.HTTP_201_CREATED)
