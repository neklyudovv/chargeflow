from django.urls import path
from rest_framework.routers import DefaultRouter

from payments.api.views import PaymentAttemptViewSet, WebhookView

router = DefaultRouter()
router.register("attempts", PaymentAttemptViewSet, basename="payment-attempts")

urlpatterns = [
    path("webhook/", WebhookView.as_view(), name="payments-webhook"),
] + router.urls
