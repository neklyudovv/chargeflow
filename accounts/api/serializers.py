from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import ApiKey, Customer, Invitation, Organization, OrganizationMembership
from config.api import ModelRequest, OptionalFieldsMixin, Request, TimeFilters

User = get_user_model()


class UserResponse(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()


class RegisterRequest(Request):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value.strip()).exists():
            raise serializers.ValidationError("This email is already registered.")
        return value.strip().lower()

    def validate_password(self, value):
        validate_password(value)
        return value


class RegisterResponse(serializers.Serializer):
    token = serializers.CharField()
    user = UserResponse()


class LoginRequest(Request):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class LoginResponse(serializers.Serializer):
    token = serializers.CharField()


class OrganizationResponse(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ["id", "name", "created_at", "updated_at", "my_role"]
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=OrganizationMembership.Role.choices, allow_null=True))
    def get_my_role(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return None
        return OrganizationMembership.objects.filter(organization=obj, user=request.user).values_list("role", flat=True).first()


class MeResponse(serializers.Serializer):
    user = UserResponse()
    organization = OrganizationResponse(allow_null=True)
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices, allow_null=True)


class CreateOrganizationRequest(ModelRequest):
    class Meta:
        model = Organization
        fields = ["name"]


class ApiKeyResponse(serializers.ModelSerializer):
    class Meta:
        model = ApiKey
        fields = ["id", "name", "prefix", "created_at", "expires_at", "revoked", "scope"]
        read_only_fields = fields


class ApiKeyCreatedResponse(ApiKeyResponse):
    key = serializers.CharField()

    class Meta(ApiKeyResponse.Meta):
        fields = ApiKeyResponse.Meta.fields + ["key"]
        read_only_fields = fields


class OrganizationCreatedResponse(serializers.Serializer):
    organization = OrganizationResponse()
    api_key = ApiKeyCreatedResponse()


class CreateApiKeyRequest(Request):
    scope = serializers.ChoiceField(choices=ApiKey.Scope.choices, default=ApiKey.Scope.READ_WRITE)
    name = serializers.CharField(max_length=100, default="Default")
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Must be a future timestamp.")
        return value


class CreateCustomerRequest(ModelRequest):
    class Meta:
        model = Customer
        fields = ["email", "name", "provider_customer_id", "provider_payment_method_id"]


class UpdateCustomerRequest(OptionalFieldsMixin, CreateCustomerRequest):
    pass


class CustomerResponse(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "email", "name", "provider_customer_id", "provider_payment_method_id", "created_at", "updated_at"]
        read_only_fields = fields


class MembershipResponse(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ["id", "user_email", "role", "joined_at"]
        read_only_fields = fields


class InvitationResponse(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = ["id", "email", "role", "expires_at", "accepted_at", "created_at"]
        read_only_fields = fields


class InvitationCreatedResponse(InvitationResponse):
    token = serializers.CharField()

    class Meta(InvitationResponse.Meta):
        fields = InvitationResponse.Meta.fields + ["token"]
        read_only_fields = fields


class CreateInvitationRequest(Request):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=[OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.MEMBER])


class AcceptInvitationRequest(Request):
    token = serializers.CharField(max_length=64)


class CustomerFilters(TimeFilters):
    email = serializers.EmailField(required=False)


class ApiKeyFilters(TimeFilters):
    scope = serializers.ChoiceField(choices=ApiKey.Scope.choices, required=False)


class MembershipFilters(TimeFilters):
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices, required=False)


class InvitationFilters(TimeFilters):
    email = serializers.EmailField(required=False)
