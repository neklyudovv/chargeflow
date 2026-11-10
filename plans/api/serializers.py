from rest_framework import serializers

from config.api import ModelRequest, OptionalFieldsMixin, TimeFilters
from plans.models import Plan, PlanStatus
from plans.validators import CURRENCY_CODES


class CreatePlanRequest(ModelRequest):
    currency = serializers.ChoiceField(choices=sorted(CURRENCY_CODES), default="USD")

    class Meta:
        model = Plan
        fields = ["name", "description", "price", "currency", "status", "interval", "trial_period_days"]


class UpdatePlanRequest(OptionalFieldsMixin, CreatePlanRequest):
    pass


class PlanResponse(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["id", "name", "description", "price", "currency", "status", "interval", "trial_period_days", "created_at", "updated_at"]
        read_only_fields = fields


class PlanFilters(TimeFilters):
    status = serializers.ChoiceField(choices=PlanStatus.choices, required=False)
    currency = serializers.ChoiceField(choices=sorted(CURRENCY_CODES), required=False)
