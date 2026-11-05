from django.urls import path
from rest_framework.routers import SimpleRouter

from catalog.views import (
    AcademicViewSet,
    AcademicSetupDraftViewSet,
    CohortTermViewSet,
    CohortViewSet,
    CurriculumModuleViewSet,
    CurriculumVersionViewSet,
    EnrollmentViewSet,
    MajorViewSet,
    ModuleViewSet,
    ModuleDefinitionViewSet,
    MyEnrollmentsView,
    SemesterViewSet,
    TeachingPeriodViewSet,
    StudentPlacementViewSet,
)

router = SimpleRouter()
router.register('academics', AcademicViewSet, basename='academic')
router.register('academic-setup-drafts', AcademicSetupDraftViewSet, basename='academic-setup-draft')
router.register('semesters', SemesterViewSet, basename='semester')
router.register('teaching-periods', TeachingPeriodViewSet, basename='teaching-period')
router.register('majors', MajorViewSet, basename='major')
router.register('module-definitions', ModuleDefinitionViewSet, basename='module-definition')
router.register('curricula', CurriculumVersionViewSet, basename='curriculum')
router.register('curriculum-modules', CurriculumModuleViewSet, basename='curriculum-module')
router.register('cohorts', CohortViewSet, basename='cohort')
router.register('cohort-terms', CohortTermViewSet, basename='cohort-term')
router.register('modules', ModuleViewSet, basename='module')
router.register('enrollments', EnrollmentViewSet, basename='enrollment')
router.register('student-placements', StudentPlacementViewSet, basename='student-placement')

urlpatterns = [
    path('me/enrollments/', MyEnrollmentsView.as_view(), name='me-enrollments'),
    *router.urls,
]
