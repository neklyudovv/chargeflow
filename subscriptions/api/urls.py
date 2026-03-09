from rest_framework.routers import DefaultRouter

from subscriptions.api.views import SubscriptionViewSet

router = DefaultRouter()
router.register("", SubscriptionViewSet, basename="subscriptions")

urlpatterns = router.urls
