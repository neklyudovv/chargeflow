from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.api.serializers import (
    AcceptInvitationRequest, ApiKeyCreatedResponse, ApiKeyFilters, ApiKeyResponse,
    CreateApiKeyRequest, CreateCustomerRequest, CreateInvitationRequest, CreateOrganizationRequest,
    CustomerFilters, CustomerResponse, InvitationCreatedResponse, InvitationFilters, InvitationResponse,
    LoginRequest, LoginResponse, MeResponse, MembershipFilters, MembershipResponse,
    OrganizationCreatedResponse, OrganizationResponse, RegisterRequest, RegisterResponse, UpdateCustomerRequest,
)
from accounts.models import ApiKey, Customer, Invitation, Organization, OrganizationMembership, User
from accounts.permissions import IsHuman, IsOrgAdmin, ProjectAccess
from accounts.request_context import get_request_organization
from accounts.throttling import AuthRateThrottle
from config.api import APIError, TimeFilters, WriteResponseMixin


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth_register"

    @extend_schema(
        operation_id="register", tags=["Authentication"], summary="Register a user",
        description="",
        request=RegisterRequest, responses={201: RegisterResponse}, auth=[],
        examples=[OpenApiExample("Register a user", value={"email": "owner@example.com", "password": "example-password-replace-before-use"}, request_only=True)],
    )
    @transaction.atomic
    def post(self, request):
        serializer = RegisterRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.create_user(username=serializer.validated_data["email"], **serializer.validated_data)
        token, _ = Token.objects.get_or_create(user=user)
        return Response(RegisterResponse({"token": token.key, "user": user}).data, status=201)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth_login"
    authentication_errors = True

    @extend_schema(
        operation_id="login", tags=["Authentication"], summary="Obtain a user token",
        description="",
        request=LoginRequest, responses={200: LoginResponse}, auth=[],
        examples=[OpenApiExample("Sign in", value={"email": "owner@example.com", "password": "example-password-replace-before-use"}, request_only=True)],
    )
    def post(self, request):
        serializer = LoginRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, username=serializer.validated_data["email"].strip().lower(), password=serializer.validated_data["password"])
        if user is None:
            response = Response({"code": "authentication_failed", "message": "Invalid email or password.", "fields": {}}, status=401)
            response["WWW-Authenticate"] = "Token"
            return response
        token, _ = Token.objects.get_or_create(user=user)
        return Response(LoginResponse({"token": token.key}).data)


@extend_schema(auth=[{"tokenAuth": []}])
class MeView(APIView):
    permission_classes = [IsHuman]
    organization_scoped = True

    @extend_schema(
        operation_id="getCurrentAccount", tags=["Account"], summary="Get the current account",
        description="",
        responses={200: MeResponse},
    )
    def get(self, request):
        organization = get_request_organization(request)
        membership = getattr(request, "membership", None)
        return Response(MeResponse({"user": request.user, "organization": organization, "role": membership.role if membership else None}, context={"request": request}).data)


@extend_schema(tags=["Organizations"], auth=[{"tokenAuth": []}])
@extend_schema_view(
    list=extend_schema(operation_id="listOrganizations", summary="List your organizations", description="", responses={200: OrganizationResponse(many=True)}),
)
class OrganizationViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = OrganizationResponse
    permission_classes = [IsHuman]
    filter_serializer_class = TimeFilters

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Organization.objects.none()
        return Organization.objects.filter(memberships__user=self.request.user)

    @extend_schema(
        operation_id="createOrganization", summary="Create an organization",
        description="",
        request=CreateOrganizationRequest, responses={201: OrganizationCreatedResponse},
        examples=[OpenApiExample("New project", value={"name": "Production"}, request_only=True)],
    )
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = CreateOrganizationRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save(owner=request.user)
        OrganizationMembership.objects.create(organization=organization, user=request.user, role=OrganizationMembership.Role.OWNER)
        key, raw = ApiKey.create_for_organization(organization, name="Default")
        return Response(OrganizationCreatedResponse({"organization": organization, "api_key": {**ApiKeyResponse(key).data, "key": raw}}, context={"request": request}).data, status=201)


@extend_schema(tags=["API keys"], auth=[{"tokenAuth": []}])
@extend_schema_view(
    list=extend_schema(operation_id="listApiKeys", summary="List API keys", description="", responses={200: ApiKeyResponse(many=True)}),
    destroy=extend_schema(operation_id="revokeApiKey", summary="Revoke an API key", description="", request=None, responses={204: None}),
)
class ApiKeyViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = ApiKeyResponse
    permission_classes = [IsHuman, ProjectAccess]
    organization_scoped = True
    filter_serializer_class = ApiKeyFilters

    def get_queryset(self):
        org = get_request_organization(self.request)
        return ApiKey.objects.filter(organization=org, revoked=False) if org is not None else ApiKey.objects.none()

    @extend_schema(
        operation_id="createApiKey", summary="Create an API key",
        description="",
        request=CreateApiKeyRequest, responses={201: ApiKeyCreatedResponse},
        examples=[OpenApiExample("Read-only integration", value={"name": "Reporting", "scope": "read"}, request_only=True)],
    )
    def create(self, request, *args, **kwargs):
        org = get_request_organization(request)
        if org is None:
            raise APIError("organization_required", "Select an organization before creating an API key.")
        serializer = CreateApiKeyRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        key, raw = ApiKey.create_for_organization(org, **serializer.validated_data)
        return Response(ApiKeyCreatedResponse({**ApiKeyResponse(key).data, "key": raw}).data, status=201)

    def destroy(self, request, *args, **kwargs):
        key = self.get_object()
        key.revoked = True
        key.save(update_fields=["revoked"])
        return Response(status=204)


@extend_schema(tags=["Customers"])
@extend_schema_view(
    list=extend_schema(operation_id="listCustomers", summary="List customers", description="", responses={200: CustomerResponse(many=True)}),
    retrieve=extend_schema(operation_id="getCustomer", summary="Get a customer", description="", responses={200: CustomerResponse}),
    create=extend_schema(operation_id="createCustomer", summary="Create a customer", description="", request=CreateCustomerRequest, responses={201: CustomerResponse}, examples=[OpenApiExample("Customer", value={"email": "customer@example.com", "name": "Example Customer"}, request_only=True)]),
    partial_update=extend_schema(operation_id="updateCustomer", summary="Update a customer", description="", request=UpdateCustomerRequest, responses={200: CustomerResponse}),
)
class CustomerViewSet(WriteResponseMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = CustomerResponse
    response_serializer_class = CustomerResponse
    request_serializers = {"create": CreateCustomerRequest, "partial_update": UpdateCustomerRequest}
    permission_classes = [ProjectAccess]
    http_method_names = ["get", "post", "patch", "head", "options"]
    organization_scoped = True
    filter_serializer_class = CustomerFilters
    filter_lookups = {"email": "email__iexact"}

    def get_queryset(self):
        org = get_request_organization(self.request)
        return Customer.objects.filter(organization=org) if org is not None else Customer.objects.none()

    def perform_create(self, serializer):
        org = get_request_organization(self.request)
        if org is None:
            raise APIError("organization_required", "Select an organization before creating a customer.")
        serializer.save(organization=org)


@extend_schema(tags=["Team"], auth=[{"tokenAuth": []}])
@extend_schema_view(list=extend_schema(operation_id="listMembers", summary="List organization members", description="", responses={200: MembershipResponse(many=True)}))
class MemberViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = MembershipResponse
    permission_classes = [IsHuman]
    organization_scoped = True
    timestamp_field = "joined_at"
    filter_serializer_class = MembershipFilters

    def get_queryset(self):
        org = get_request_organization(self.request)
        return OrganizationMembership.objects.select_related("user").filter(organization=org) if org is not None else OrganizationMembership.objects.none()


@extend_schema(tags=["Team"], auth=[{"tokenAuth": []}])
@extend_schema_view(list=extend_schema(operation_id="listInvitations", summary="List invitations", description="", responses={200: InvitationResponse(many=True)}))
class InvitationViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = InvitationResponse
    permission_classes = [IsHuman, IsOrgAdmin]
    organization_scoped = True
    filter_serializer_class = InvitationFilters
    filter_lookups = {"email": "email__iexact"}

    def get_queryset(self):
        org = get_request_organization(self.request)
        return Invitation.objects.filter(organization=org, accepted_at__isnull=True) if org is not None else Invitation.objects.none()

    @extend_schema(
        operation_id="createInvitation", summary="Invite a team member",
        description="",
        request=CreateInvitationRequest, responses={201: InvitationCreatedResponse},
        examples=[OpenApiExample("Invite a member", value={"email": "member@example.com", "role": "member"}, request_only=True)],
    )
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        org = get_request_organization(request)
        serializer = CreateInvitationRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        if OrganizationMembership.objects.filter(organization=org, user__email__iexact=serializer.validated_data["email"]).exists():
            raise ValidationError({"email": ["This user is already a member of this organization."]})
        invitation = Invitation.create_for_organization(org, serializer.validated_data["email"], serializer.validated_data["role"])
        return Response(InvitationCreatedResponse(invitation).data, status=201)


@extend_schema(auth=[{"tokenAuth": []}])
class AcceptInvitationView(APIView):
    permission_classes = [IsHuman]

    @extend_schema(
        operation_id="acceptInvitation", tags=["Team"], summary="Accept an invitation",
        description="",
        request=AcceptInvitationRequest, responses={201: MembershipResponse},
    )
    @transaction.atomic
    def post(self, request):
        serializer = AcceptInvitationRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invitation = Invitation.objects.select_related("organization").get(token=serializer.validated_data["token"])
        except Invitation.DoesNotExist:
            raise ValidationError({"token": ["Invalid invitation token."]})
        if invitation.is_accepted:
            raise ValidationError({"token": ["This invitation has already been accepted."]})
        if invitation.is_expired:
            raise ValidationError({"token": ["This invitation has expired."]})
        if invitation.email.lower() != request.user.email.lower():
            raise ValidationError({"token": ["This invitation was sent to a different email address."]})
        if OrganizationMembership.objects.filter(organization=invitation.organization, user=request.user).exists():
            raise ValidationError({"token": ["You are already a member of this organization."]})
        membership = OrganizationMembership.objects.create(organization=invitation.organization, user=request.user, role=invitation.role)
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])
        return Response(MembershipResponse(membership).data, status=201)
