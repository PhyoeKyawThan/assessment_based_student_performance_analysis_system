from rest_framework.routers import SimpleRouter

from marking.views import (
    AssessmentResultViewSet,
    ModulePeriodResultViewSet,
    ModuleResultSheetViewSet,
)

router = SimpleRouter()
router.register('assessment-results', AssessmentResultViewSet, basename='assessment-result')
router.register('module-result-sheets', ModuleResultSheetViewSet, basename='module-result-sheet')
router.register('module-period-results', ModulePeriodResultViewSet, basename='module-period-result')

urlpatterns = router.urls
