from django.urls import path
from rest_framework.routers import SimpleRouter

from catalog.views import (
    AcademicViewSet,
    EnrollmentViewSet,
    ModuleViewSet,
    MyEnrollmentsView,
    SemesterViewSet,
)

router = SimpleRouter()
router.register('academics', AcademicViewSet, basename='academic')
router.register('semesters', SemesterViewSet, basename='semester')
router.register('modules', ModuleViewSet, basename='module')
router.register('enrollments', EnrollmentViewSet, basename='enrollment')

urlpatterns = [
    path('me/enrollments/', MyEnrollmentsView.as_view(), name='me-enrollments'),
    *router.urls,
]
