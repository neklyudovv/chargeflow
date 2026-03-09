from rest_framework import serializers
from django.utils import timezone

from accounts.models import ApiKey, Customer, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)


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
