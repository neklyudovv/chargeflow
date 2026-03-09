from rest_framework.routers import DefaultRouter

from accounts.api.views import CustomerViewSet, OrganizationViewSet

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("customers", CustomerViewSet, basename="customers")

urlpatterns = router.urls
