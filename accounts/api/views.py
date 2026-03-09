from rest_framework import viewsets

from accounts.api.serializers import CustomerSerializer, OrganizationSerializer
from accounts.models import Customer, Organization


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.select_related("organization").all()
    serializer_class = CustomerSerializer
