from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets

from accounts.request_context import get_request_organization
from invoices.api.serializers import InvoiceFilters, InvoiceResponse
from invoices.domain.models import Invoice


@extend_schema(tags=["Invoices"])
@extend_schema_view(
    list=extend_schema(operation_id="listInvoices", summary="List invoices", description="", responses={200: InvoiceResponse(many=True)}),
    retrieve=extend_schema(operation_id="getInvoice", summary="Get an invoice", description="", responses={200: InvoiceResponse}),
)
class InvoiceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = InvoiceResponse
    organization_scoped = True
    filter_serializer_class = InvoiceFilters
    filter_lookups = {"customer_id": "subscription__customer_id"}

    def get_queryset(self):
        org = get_request_organization(self.request)
        if org is None:
            return Invoice.objects.none()
        return Invoice.objects.select_related("subscription").prefetch_related("lines").filter(organization=org)
