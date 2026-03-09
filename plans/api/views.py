from rest_framework import viewsets

from plans.api.serializers import PlanSerializer
from plans.models import Plan


class PlanViewSet(viewsets.ModelViewSet):
    queryset = Plan.objects.all()
    serializer_class = PlanSerializer
