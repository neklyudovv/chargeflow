from rest_framework import viewsets
from rest_framework.response import Response

from infrastructure.events import event_bus
from plans.api.serializers import PlanSerializer
from plans.events import PlanArchived, PlanCreated, PlanDeleted, PlanUpdated
from plans.models import Plan, PlanStatus


class PlanViewSet(viewsets.ModelViewSet):
    serializer_class = PlanSerializer

    def get_queryset(self):
        return Plan.objects.filter(organization=self.request.user)

    def perform_create(self, serializer):
        plan = serializer.save(organization=self.request.user)
        event_bus.publish(PlanCreated(plan_id=plan.pk))

    def perform_update(self, serializer):
        plan = serializer.save()
        if plan.status == PlanStatus.ARCHIVED:
            event_bus.publish(PlanArchived(plan_id=plan.pk))
        else:
            event_bus.publish(PlanUpdated(plan_id=plan.pk))

    def perform_destroy(self, instance):
        plan_id = instance.pk
        instance.delete()
        event_bus.publish(PlanDeleted(plan_id=plan_id))
