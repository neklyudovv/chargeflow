from rest_framework import serializers

from subscriptions.domain.models import Subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = [
            "id",
            "customer",
            "plan",
            "status",
            "current_period_start",
            "current_period_end",
            "canceled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "current_period_start",
            "current_period_end",
            "canceled_at",
            "created_at",
            "updated_at",
        ]


class SubscriptionCreateSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    plan_id = serializers.IntegerField()
