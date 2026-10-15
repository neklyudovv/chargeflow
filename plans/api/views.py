from django.db.models import ProtectedError
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from accounts.request_context import get_request_organization
from plans.api.serializers import PlanSerializer
from plans.models import Plan


class PlanViewSet(viewsets.ModelViewSet):
    serializer_class = PlanSerializer

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Plan.objects.none()
        return Plan.objects.filter(organization=org)

    def perform_create(self, serializer):
        org = get_request_organization(self.request)
        if org is None:
            raise ValidationError("No organization in scope.")
        serializer.save(organization=org)

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            # subscriptions FK is PROTECT - a plan with subscriptions cannot be
            # deleted. Archive it instead of hard-deleting.
            raise ValidationError(
                {"detail": "Cannot delete a plan that has subscriptions; archive it instead."}
            )
