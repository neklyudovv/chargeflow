from django.urls import path
from rest_framework.routers import DefaultRouter

from accounts.api.views import ApiKeyViewSet, CustomerViewSet, MeView, OrganizationViewSet

router = DefaultRouter()
router.register("keys", ApiKeyViewSet, basename="api-keys")
router.register("customers", CustomerViewSet, basename="customers")
router.register("organizations", OrganizationViewSet, basename="organizations")

urlpatterns = [
    path("me/", MeView.as_view(), name="accounts-me"),
    *router.urls,
]
