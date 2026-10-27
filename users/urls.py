from django.urls import path
from rest_framework.routers import SimpleRouter

from users.views import MePushTokenView, MeView, UserViewSet

router = SimpleRouter()
router.register('users', UserViewSet, basename='user')

urlpatterns = [
    path('me/', MeView.as_view(), name='me'),
    path('me/push-token/', MePushTokenView.as_view(), name='me-push-token'),
    *router.urls,
]
