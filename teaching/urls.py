from django.urls import path
from rest_framework.routers import SimpleRouter

from teaching.views import (
    AssessmentDistributionViewSet,
    AssessmentViewSet,
    LecturePlanViewSet,
    MyLecturePlansView,
)

router = SimpleRouter()
router.register('lecture-plans', LecturePlanViewSet, basename='lecture-plan')
router.register(
    'assessment-distributions', AssessmentDistributionViewSet, basename='assessment-distribution'
)
router.register('assessments', AssessmentViewSet, basename='assessment')

urlpatterns = [
    path('me/lecture-plans/', MyLecturePlansView.as_view(), name='me-lecture-plans'),
    *router.urls,
]
