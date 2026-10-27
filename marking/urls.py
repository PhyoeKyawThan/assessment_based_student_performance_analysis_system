from rest_framework.routers import SimpleRouter

from marking.views import AssessmentResultViewSet

router = SimpleRouter()
router.register('assessment-results', AssessmentResultViewSet, basename='assessment-result')

urlpatterns = router.urls
