from rest_framework import viewsets

from plans.api.serializers import PlanSerializer
from plans.models import Plan


class PlanViewSet(viewsets.ModelViewSet):
    serializer_class = PlanSerializer

    def get_queryset(self):
        return Plan.objects.filter(organization=self.request.user)

    def perform_create(self, serializer):
        serializer.save(organization=self.request.user)
