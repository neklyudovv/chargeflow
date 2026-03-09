from rest_framework.routers import DefaultRouter

from plans.api.views import PlanViewSet

router = DefaultRouter()
router.register("", PlanViewSet, basename="plans")

urlpatterns = router.urls
