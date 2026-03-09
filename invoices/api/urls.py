from rest_framework.routers import DefaultRouter

from invoices.api.views import InvoiceViewSet

router = DefaultRouter()
router.register("", InvoiceViewSet, basename="invoices")

urlpatterns = router.urls
