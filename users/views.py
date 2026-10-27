from django.db.models import ProtectedError, Q
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenViewBase

from core.exceptions import ConflictError
from core.views import RepresentationMixin
from users.authentication import (
    REFRESH_COOKIE,
    clear_token_cookies,
    is_web_client,
    set_token_cookies,
)
from users.models import User
from users.permissions import IsAdminRole
from users.serializers import (
    MeUpdateSerializer,
    PushTokenSerializer,
    UserAdminUpdateSerializer,
    UserCreateSerializer,
    UserRepresentationSerializer,
)


class CustomTokenObtainPairView(TokenObtainPairView):
    """Web clients get HttpOnly cookies; every other caller gets the raw token pair."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200 and is_web_client(request):
            set_token_cookies(response, response.data['access'], response.data['refresh'])
            response.data = {'message': 'Login successful (HttpOnly cookies set)'}
        return response


class CustomTokenRefreshView(TokenViewBase):
    serializer_class = TokenRefreshSerializer

    def post(self, request, *args, **kwargs):
        web = is_web_client(request)
        if web:
            cookie_value = request.COOKIES.get(REFRESH_COOKIE)
            if cookie_value is None:
                raise InvalidToken('Refresh token cookie is missing.')
            data = {'refresh': cookie_value}
        else:
            data = request.data

        serializer = self.get_serializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])

        tokens = serializer.validated_data
        if web:
            response = Response({'message': 'Token refreshed'})
            set_token_cookies(response, tokens['access'], tokens['refresh'])
            return response
        return Response(tokens)


class LogoutView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        raw_token = request.data.get('refresh') or request.COOKIES.get(REFRESH_COOKIE)
        if raw_token is not None:
            try:
                RefreshToken(raw_token).blacklist()
            except TokenError as exc:
                raise InvalidToken(exc.args[0])

        response = Response(status=204)
        if is_web_client(request):
            clear_token_cookies(response)
        return response


class MeView(APIView):
    def get(self, request):
        return Response(UserRepresentationSerializer(request.user).data)

    def patch(self, request):
        serializer = MeUpdateSerializer(instance=request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserRepresentationSerializer(request.user).data)


class MePushTokenView(APIView):
    """POST registers this device's Expo push token, DELETE clears it."""

    def post(self, request):
        serializer = PushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request.user.push_token = serializer.validated_data['push_token']
        request.user.save(update_fields=['push_token'])
        return Response({'has_push_token': True})

    def delete(self, request):
        request.user.push_token = ''
        request.user.save(update_fields=['push_token'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(RepresentationMixin, viewsets.ModelViewSet):
    permission_classes = (IsAdminRole,)
    queryset = User.objects.all()
    serializer_class = UserRepresentationSerializer
    representation_serializer_class = UserRepresentationSerializer

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in {'update', 'partial_update'}:
            return UserAdminUpdateSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = User.objects.all()
        params = self.request.query_params

        role = params.get('role')
        if role:
            queryset = queryset.filter(role=role)

        is_active = params.get('is_active')
        if is_active is not None and is_active != '':
            queryset = queryset.filter(is_active=is_active.lower() in {'true', '1'})

        search = params.get('search')
        if search:
            queryset = queryset.filter(
                Q(full_name__icontains=search)
                | Q(email__icontains=search)
                | Q(username__icontains=search)
            )

        return queryset.order_by('date_joined')

    def perform_destroy(self, instance):
        if instance.enrollments.exists():
            raise ConflictError(
                'User is enrolled in modules. Send is_active: false to deactivate instead.',
                fields={'id': ['This user still has enrollments.']},
            )
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ConflictError(str(exc))
