from rest_framework import mixins, viewsets

from accounts.request_context import get_request_organization
from invoices.api.serializers import InvoiceSerializer
from invoices.domain.models import Invoice


class InvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = InvoiceSerializer

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Invoice.objects.none()
        return (
            Invoice.objects.select_related("subscription")
            .prefetch_related("lines")
            .filter(subscription__customer__organization=org)
        )
