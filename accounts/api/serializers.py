from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers

from accounts.models import ApiKey, Customer, Organization

User = get_user_model()


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value.strip()).exists():
            raise serializers.ValidationError("This email is already registered.")
        return value.strip().lower()

    def validate_password(self, value):
        validate_password(value)
        return value


class OrganizationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["name"]


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class ApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiKey
        fields = ["id", "name", "prefix", "created_at", "expires_at", "revoked"]
        read_only_fields = ["id", "prefix", "created_at", "expires_at", "revoked"]


class ApiKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, default="Default")
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_expires_at(self, value):
        if value is not None:
            if value <= timezone.now():
                raise serializers.ValidationError("expires_at must be a future date.")
        return value


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "email", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
