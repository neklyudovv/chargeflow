from rest_framework import serializers

from accounts.models import Customer, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "organization", "email", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
