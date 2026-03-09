from rest_framework import mixins, viewsets

from invoices.api.serializers import InvoiceSerializer
from invoices.domain.models import Invoice


class InvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = InvoiceSerializer

    def get_queryset(self):
        return (
            Invoice.objects.select_related("subscription")
            .prefetch_related("lines")
            .filter(subscription__customer__organization=self.request.user)
        )
