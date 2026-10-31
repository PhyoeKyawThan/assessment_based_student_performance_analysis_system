from django.urls import path
from rest_framework.routers import SimpleRouter

from catalog.views import (
    AcademicViewSet,
    AcademicSetupDraftViewSet,
    EnrollmentViewSet,
    MajorViewSet,
    ModuleViewSet,
    MyEnrollmentsView,
    SemesterViewSet,
    StudentPlacementViewSet,
)

router = SimpleRouter()
router.register('academics', AcademicViewSet, basename='academic')
router.register('academic-setup-drafts', AcademicSetupDraftViewSet, basename='academic-setup-draft')
router.register('semesters', SemesterViewSet, basename='semester')
router.register('majors', MajorViewSet, basename='major')
router.register('modules', ModuleViewSet, basename='module')
router.register('enrollments', EnrollmentViewSet, basename='enrollment')
router.register('student-placements', StudentPlacementViewSet, basename='student-placement')

urlpatterns = [
    path('me/enrollments/', MyEnrollmentsView.as_view(), name='me-enrollments'),
    *router.urls,
]
