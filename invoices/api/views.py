from rest_framework import mixins, viewsets

from invoices.api.serializers import InvoiceSerializer
from invoices.domain.models import Invoice


class InvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Invoice.objects.select_related("subscription").prefetch_related("lines").all()
    serializer_class = InvoiceSerializer
