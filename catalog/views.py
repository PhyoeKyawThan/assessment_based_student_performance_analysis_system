from django.db.models import ProtectedError, Q
from django.db.transaction import atomic
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from catalog.models import Academic, Enrollment, Module, Semester
from catalog.serializers import (
    AcademicSerializer,
    EnrollmentBulkSerializer,
    EnrollmentSerializer,
    EnrollmentStatusSerializer,
    ModuleSerializer,
    SemesterSerializer,
)
from core.exceptions import ConflictError
from core.filters import bool_param, uuid_param
from core.views import RepresentationMixin
from users.models import User
from users.permissions import IsAdminRole, IsAdminRoleOrReadOnly, IsStudentRole


class AcademicViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Academic.objects.all()
    serializer_class = AcademicSerializer
    representation_serializer_class = AcademicSerializer
    permission_classes = (IsAdminRoleOrReadOnly,)

    def get_queryset(self):
        queryset = super().get_queryset()
        raw = self.request.query_params.get('is_current')
        if raw not in (None, ''):
            queryset = queryset.filter(is_current=bool_param('is_current', raw))
        return queryset

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ConflictError(
                'Semesters still reference this academic year. Delete them first.'
            )


class SemesterViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Semester.objects.select_related('academic')
    serializer_class = SemesterSerializer
    representation_serializer_class = SemesterSerializer
    permission_classes = (IsAdminRoleOrReadOnly,)

    def get_queryset(self):
        queryset = super().get_queryset()
        raw = self.request.query_params.get('academic_id')
        if raw not in (None, ''):
            queryset = queryset.filter(academic=uuid_param('academic_id', raw))
        return queryset

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ConflictError('Modules still reference this semester. Delete them first.')


class ModuleViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Module.objects.select_related(
        'semester__academic', 'lecture_plan__lecturer'
    )
    serializer_class = ModuleSerializer
    representation_serializer_class = ModuleSerializer
    permission_classes = (IsAdminRoleOrReadOnly,)

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        if self.request.user.role == User.Role.STUDENT:
            queryset = queryset.filter(enrollments__student=self.request.user)

        raw = params.get('semester_id')
        if raw not in (None, ''):
            queryset = queryset.filter(semester=uuid_param('semester_id', raw))

        raw = params.get('academic_id')
        if raw not in (None, ''):
            queryset = queryset.filter(semester__academic=uuid_param('academic_id', raw))

        raw = params.get('lecturer_id')
        if raw not in (None, ''):
            queryset = queryset.filter(lecture_plan__lecturer=uuid_param('lecturer_id', raw))

        raw = params.get('current_year')
        if raw not in (None, ''):
            queryset = queryset.filter(
                semester__academic__is_current=bool_param('current_year', raw)
            )

        raw = params.get('planned')
        if raw not in (None, ''):
            queryset = queryset.filter(
                lecture_plan__isnull=not bool_param('planned', raw)
            )

        search = params.get('search')
        if search:
            queryset = queryset.filter(
                Q(module_code__icontains=search) | Q(module_name__icontains=search)
            )
        return queryset

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ConflictError(
                'Enrollments still reference this module. Drop or complete them first.'
            )


class EnrollmentViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Enrollment.objects.select_related('student', 'module')
    serializer_class = EnrollmentSerializer
    representation_serializer_class = EnrollmentSerializer
    permission_classes = (IsAdminRoleOrReadOnly,)

    def get_serializer_class(self):
        if self.action in ('update', 'partial_update'):
            return EnrollmentStatusSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        user = self.request.user

        if user.role == User.Role.STUDENT:
            queryset = queryset.filter(student=user)
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(module__lecture_plan__lecturer=user)

        for param, lookup in (
            ('module_id', 'module'),
            ('student_id', 'student'),
            ('academic_id', 'module__semester__academic'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=False, methods=['post'], permission_classes=(IsAdminRole,))
    def bulk(self, request):
        serializer = EnrollmentBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        module_id = serializer.validated_data['module_id']
        student_ids = list(dict.fromkeys(serializer.validated_data['student_ids']))

        module = Module.objects.filter(pk=module_id).first()
        if module is None:
            raise ValidationError({'module_id': ['Module not found.']})

        found = {user.pk: user for user in User.objects.filter(pk__in=student_ids)}
        unknown = [str(pk) for pk in student_ids if pk not in found]
        not_students = [
            str(pk) for pk, user in found.items() if user.role != User.Role.STUDENT
        ]
        if unknown or not_students:
            messages = [f'Unknown user {pk}.' for pk in unknown]
            messages += [f'{pk} is not a STUDENT.' for pk in not_students]
            raise ValidationError({'student_ids': messages})

        with atomic():
            already = set(
                Enrollment.objects.filter(
                    module=module, student__in=found.values()
                ).values_list('student_id', flat=True)
            )
            created = [
                Enrollment.objects.create(module=module, student=found[pk])
                for pk in student_ids
                if pk not in already
            ]

        return Response({
            'created': [str(enrollment.pk) for enrollment in created],
            'skipped': [
                {'student_id': str(pk), 'reason': 'already_enrolled'}
                for pk in student_ids
                if pk in already
            ],
        })


class MyEnrollmentsView(ListAPIView):
    permission_classes = (IsStudentRole,)
    serializer_class = EnrollmentSerializer

    def get_queryset(self):
        return Enrollment.objects.filter(student=self.request.user).select_related(
            'student', 'module'
        )
