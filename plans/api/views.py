from django.db.models import ProtectedError
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework import viewsets

from accounts.permissions import ProjectAccess
from accounts.request_context import get_request_organization
from config.api import APIError, WriteResponseMixin
from plans.api.serializers import CreatePlanRequest, PlanFilters, PlanResponse, UpdatePlanRequest
from plans.models import Plan


@extend_schema(tags=["Plans"])
@extend_schema_view(
    list=extend_schema(operation_id="listPlans", summary="List plans", description="", responses={200: PlanResponse(many=True)}),
    retrieve=extend_schema(operation_id="getPlan", summary="Get a plan", description="", responses={200: PlanResponse}),
    create=extend_schema(operation_id="createPlan", summary="Create a plan", description="", request=CreatePlanRequest, responses={201: PlanResponse}, examples=[OpenApiExample("Monthly plan", value={"name": "Pro", "price": "19.99", "currency": "USD", "interval": "month", "trial_period_days": 7}, request_only=True)]),
    partial_update=extend_schema(operation_id="updatePlan", summary="Update or archive a plan", description="", request=UpdatePlanRequest, responses={200: PlanResponse}),
    destroy=extend_schema(operation_id="deletePlan", summary="Delete an unused plan", description="", request=None, responses={204: None}),
)
class PlanViewSet(WriteResponseMixin, viewsets.ModelViewSet):
    permission_classes = [ProjectAccess]
    serializer_class = PlanResponse
    response_serializer_class = PlanResponse
    request_serializers = {"create": CreatePlanRequest, "partial_update": UpdatePlanRequest}
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    organization_scoped = True
    filter_serializer_class = PlanFilters

    def get_queryset(self):
        org = get_request_organization(self.request)
        return Plan.objects.filter(organization=org) if org is not None else Plan.objects.none()

    def perform_create(self, serializer):
        org = get_request_organization(self.request)
        if org is None:
            raise APIError("organization_required", "Select an organization before creating a plan.")
        serializer.save(organization=org)

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise APIError("invalid_transition", "A plan referenced by subscriptions cannot be deleted. Archive it instead.")
