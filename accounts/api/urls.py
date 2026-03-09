from django.urls import path
from rest_framework.routers import DefaultRouter

from accounts.api.views import ApiKeyViewSet, CustomerViewSet, OrganizationMeView

router = DefaultRouter()
router.register("keys", ApiKeyViewSet, basename="api-keys")
router.register("customers", CustomerViewSet, basename="customers")

urlpatterns = [
    path("me/", OrganizationMeView.as_view(), name="organization-me"),
    *router.urls,
]
