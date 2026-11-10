from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter

from accounts.api.views import AcceptInvitationView, ApiKeyViewSet, CustomerViewSet, InvitationViewSet, LoginView, MemberViewSet, MeView, OrganizationViewSet, RegisterView
from config.health import HealthView
from invoices.api.views import InvoiceViewSet
from payments.api.views import PaymentAttemptViewSet, WebhookView
from plans.api.views import PlanViewSet
from subscriptions.api.views import SubscriptionViewSet

router = SimpleRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("api-keys", ApiKeyViewSet, basename="api-keys")
router.register("members", MemberViewSet, basename="members")
router.register("invitations", InvitationViewSet, basename="invitations")
router.register("customers", CustomerViewSet, basename="customers")
router.register("plans", PlanViewSet, basename="plans")
router.register("subscriptions", SubscriptionViewSet, basename="subscriptions")
router.register("invoices", InvoiceViewSet, basename="invoices")
router.register("payment-attempts", PaymentAttemptViewSet, basename="payment-attempts")

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/v1/auth/register/", RegisterView.as_view(), name="register"),
    path("api/v1/auth/login/", LoginView.as_view(), name="login"),
    path("api/v1/me/", MeView.as_view(), name="me"),
    path("api/v1/invitations/accept/", AcceptInvitationView.as_view(), name="invitations-accept"),
    path("api/v1/webhooks/payments/", WebhookView.as_view(), name="payments-webhook"),
    path("api/v1/", include(router.urls)),
]
