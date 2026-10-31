from django.db.models import ProtectedError, Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
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


def academic_history(student):
    """Stable nested result history grouped by the offering's academic year and semester."""
    enrollments = student.enrollments.select_related(
        'module__semester__academic',
        'module__lecture_plan__lecturer',
    ).prefetch_related(
        'results__assessment__distribution',
        'results__graded_by',
    ).order_by(
        '-module__semester__academic__start_date',
        '-module__semester__semester_no',
        'module__module_code',
    )
    years = {}
    for enrollment in enrollments:
        module = enrollment.module
        semester = module.semester
        academic = semester.academic
        year = years.setdefault(str(academic.id), {
            'academic_id': str(academic.id),
            'academic_year': academic.academic_year,
            'semesters': {},
        })
        term = year['semesters'].setdefault(str(semester.id), {
            'semester_id': str(semester.id),
            'semester_no': semester.semester_no,
            'semester_name': semester.semester_name,
            'modules': [],
        })
        results = []
        obtained_total = 0
        maximum_total = 0
        for result in sorted(
            enrollment.results.all(),
            key=lambda row: (
                row.assessment.distribution.display_order,
                row.assessment.sequence_no,
            ),
        ):
            obtained_total += result.marks_obtained
            maximum_total += result.assessment.max_mark
            results.append({
                'id': str(result.id),
                'assessment_id': str(result.assessment_id),
                'assessment_type': result.assessment.distribution.assessment_type,
                'title': result.assessment.title,
                'sequence_no': result.assessment.sequence_no,
                'marks_obtained': f'{result.marks_obtained:.2f}',
                'max_mark': f'{result.assessment.max_mark:.2f}',
                'remark': result.remark,
                'graded_at': result.graded_at.isoformat(),
                'graded_by': result.graded_by.full_name,
            })
        plan = getattr(module, 'lecture_plan', None)
        term['modules'].append({
            'enrollment_id': str(enrollment.id),
            'module_id': str(module.id),
            'module_code': module.module_code,
            'module_name': module.module_name,
            'study_year': module.study_year,
            'status': enrollment.status,
            'teacher': plan.lecturer.full_name if plan is not None else None,
            'obtained_total': f'{obtained_total:.2f}',
            'maximum_total': f'{maximum_total:.2f}',
            'results': results,
        })
    return {
        'student': UserRepresentationSerializer(student).data,
        'academic_years': [
            {**year, 'semesters': list(year['semesters'].values())}
            for year in years.values()
        ],
    }


class MyAcademicHistoryView(APIView):
    def get(self, request):
        if request.user.role != User.Role.STUDENT:
            return Response(
                {'code': 'permission_denied', 'detail': 'Only students have an academic history.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(academic_history(request.user))


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

    @action(detail=True, methods=['get'], url_path='academic-history')
    def academic_history(self, request, pk=None):
        student = self.get_object()
        if student.role != User.Role.STUDENT:
            return Response(
                {'code': 'validation_error', 'detail': 'This user is not a student.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(academic_history(student))
