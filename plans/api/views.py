from django.db.models import ProtectedError
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from accounts.request_context import get_request_organization
from infrastructure.events import event_dispatcher
from plans.api.serializers import PlanSerializer
from plans.events import PlanArchived, PlanCreated, PlanDeleted, PlanUpdated
from plans.models import Plan, PlanStatus


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
        plan = serializer.save(organization=org)
        event_dispatcher.publish(PlanCreated(plan_id=plan.pk))

    def perform_update(self, serializer):
        plan = serializer.save()
        if plan.status == PlanStatus.ARCHIVED:
            event_dispatcher.publish(PlanArchived(plan_id=plan.pk))
        else:
            event_dispatcher.publish(PlanUpdated(plan_id=plan.pk))

    def perform_destroy(self, instance):
        plan_id = instance.pk
        try:
            instance.delete()
        except ProtectedError:
            # subscriptions FK is PROTECT - a plan with subscriptions cannot be
            # deleted. Archive it instead of hard-deleting.
            raise ValidationError(
                {"detail": "Cannot delete a plan that has subscriptions; archive it instead."}
            )
        event_dispatcher.publish(PlanDeleted(plan_id=plan_id))
