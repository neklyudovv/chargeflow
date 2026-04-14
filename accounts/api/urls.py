from django.urls import path
from rest_framework.routers import DefaultRouter

from accounts.api.views import (
    AcceptInvitationView,
    ApiKeyViewSet,
    CustomerViewSet,
    InvitationViewSet,
    MemberViewSet,
    MeView,
    OrganizationViewSet,
)

router = DefaultRouter()
router.register("keys", ApiKeyViewSet, basename="api-keys")
router.register("customers", CustomerViewSet, basename="customers")
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("members", MemberViewSet, basename="members")
router.register("invitations", InvitationViewSet, basename="invitations")

urlpatterns = [
    path("me/", MeView.as_view(), name="accounts-me"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invitations-accept"),
    *router.urls,
]
