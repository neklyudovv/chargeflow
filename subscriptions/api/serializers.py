from rest_framework import serializers

from accounts.models import Customer
from accounts.request_context import get_request_organization
from config.api import Request, TimeFilters
from plans.models import Plan
from subscriptions.domain.models import Subscription, SubscriptionStatus


class SubscriptionResponse(serializers.ModelSerializer):
    customer_id = serializers.IntegerField(read_only=True)
    plan_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Subscription
        fields = ["id", "customer_id", "plan_id", "status", "current_period_start", "current_period_end", "canceled_at", "canceled_reason", "created_at", "updated_at"]
        read_only_fields = fields


class CreateSubscriptionRequest(Request):
    customer_id = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.none(), source="customer", pk_field=serializers.IntegerField(min_value=1))
    plan_id = serializers.PrimaryKeyRelatedField(queryset=Plan.objects.none(), source="plan", pk_field=serializers.IntegerField(min_value=1))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        org = get_request_organization(request) if request is not None else None
        if org is not None:
            self.fields["customer_id"].queryset = Customer.objects.filter(organization=org)
            self.fields["plan_id"].queryset = Plan.objects.filter(organization=org)


class SubscriptionFilters(TimeFilters):
    status = serializers.ChoiceField(choices=SubscriptionStatus.choices, required=False)
    customer_id = serializers.IntegerField(min_value=1, required=False)
    plan_id = serializers.IntegerField(min_value=1, required=False)
