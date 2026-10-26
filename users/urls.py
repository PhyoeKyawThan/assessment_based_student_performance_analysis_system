from django.urls import path
from rest_framework.routers import SimpleRouter

from users.views import MeView, UserViewSet

router = SimpleRouter()
router.register('users', UserViewSet, basename='user')

urlpatterns = [
    path('me/', MeView.as_view(), name='me'),
    *router.urls,
]
