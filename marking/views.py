from django.db.models import Count, Q
from django.db.transaction import atomic
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.filters import uuid_param
from core.views import RepresentationMixin
from catalog.models import Enrollment
from marking.models import (
    AssessmentResult,
    ModulePeriodResult,
    ModuleResultSheet,
    ResultWorkflowEvent,
)
from marking.notifications import notify_marks_recorded
from marking.permissions import IsResultWriter
from marking.serializers import (
    AssessmentResultBulkSerializer,
    AssessmentResultSerializer,
    ModulePeriodResultBulkSerializer,
    ModulePeriodResultSerializer,
    ModuleResultSheetSerializer,
)
from teaching.permissions import is_admin
from users.models import User


class AssessmentResultViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = AssessmentResult.objects.select_related(
        'assessment__distribution__lecture_plan',
        'enrollment__student',
        'enrollment__module',
        'graded_by',
    )
    serializer_class = AssessmentResultSerializer
    representation_serializer_class = AssessmentResultSerializer
    permission_classes = (IsResultWriter,)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if is_admin(user) or is_student_affairs(user):
            pass
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(assessment__distribution__lecture_plan__lecturer=user)
        else:
            queryset = queryset.filter(enrollment__student=user)

        params = self.request.query_params
        for param, lookup in (
            ('assessment_id', 'assessment'),
            ('enrollment_id', 'enrollment'),
            ('module_id', 'enrollment__module'),
            ('student_id', 'enrollment__student'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})
        return queryset

    def perform_create(self, serializer):
        notify_marks_recorded([serializer.save()])

    def perform_update(self, serializer):
        notify_marks_recorded([serializer.save()])

    @action(detail=False, methods=['post'])
    def bulk(self, request):
        serializer = AssessmentResultBulkSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        with atomic():
            results = serializer.save()
        notify_marks_recorded(results)
        return Response([
            self.representation_serializer_class(result).data for result in results
        ])


def is_student_affairs(user):
    return user.role == User.Role.STUDENT_AFFAIRS


class ModuleResultSheetViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ModuleResultSheetSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        queryset = ModuleResultSheet.objects.select_related(
            'lecture_plan__module__semester__academic',
            'lecture_plan__lecturer',
            'lecture_plan__academic',
        ).annotate(
            enrolled_count=Count(
                'lecture_plan__module__enrollments',
                filter=Q(lecture_plan__module__enrollments__status__in=(
                    Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED,
                )),
                distinct=True,
            ),
            marked_count=Count(
                'results',
                filter=Q(results__enrollment__status__in=(
                    Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED,
                )),
                distinct=True,
            ),
        )
        if self.action != 'list':
            queryset = queryset.prefetch_related(
                'results__enrollment__student', 'workflow_events__actor'
            )
        if is_admin(user) or is_student_affairs(user):
            pass
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(lecture_plan__lecturer=user)
        else:
            return queryset.none()

        params = self.request.query_params
        for param, lookup in (
            ('academic_id', 'lecture_plan__academic'),
            ('semester_id', 'lecture_plan__module__semester'),
            ('module_id', 'lecture_plan__module'),
            ('lecturer_id', 'lecture_plan__lecturer'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        return queryset

    def _ensure_teacher_writer(self, sheet):
        user = self.request.user
        if not (is_admin(user) or sheet.lecture_plan.lecturer_id == user.pk):
            raise PermissionDenied('Only the assigned lecturer or an admin may edit this sheet.')

    def _ensure_reviewer(self):
        if not (is_admin(self.request.user) or is_student_affairs(self.request.user)):
            raise PermissionDenied('Only Student Affairs or an admin may review result sheets.')

    @staticmethod
    def _transition(sheet, actor, to_status, comment=''):
        old_status = sheet.status
        sheet.status = to_status
        sheet.save()
        ResultWorkflowEvent.objects.create(
            sheet=sheet, actor=actor, from_status=old_status,
            to_status=to_status, comment=comment,
        )

    @action(detail=True, methods=['post'], url_path='save-marks')
    def save_marks(self, request, pk=None):
        sheet = self.get_object()
        self._ensure_teacher_writer(sheet)
        serializer = ModulePeriodResultBulkSerializer(
            data=request.data, context={'request': request, 'sheet': sheet}
        )
        serializer.is_valid(raise_exception=True)
        with atomic():
            results = serializer.save()
        return Response(ModulePeriodResultSerializer(results, many=True).data)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        sheet = self.get_object()
        self._ensure_teacher_writer(sheet)
        if sheet.status not in (ModuleResultSheet.Status.DRAFT, ModuleResultSheet.Status.RETURNED):
            raise ValidationError('Only a DRAFT or RETURNED sheet can be submitted.')
        enrollment_count = Enrollment.objects.filter(
            module=sheet.lecture_plan.module,
            status__in=(Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED),
        ).count()
        marked_count = sheet.results.filter(
            enrollment__status__in=(Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED)
        ).count()
        if enrollment_count == 0 or marked_count != enrollment_count:
            raise ValidationError({
                'results': [f'All enrolled students require a mark ({marked_count}/{enrollment_count}).']
            })
        with atomic():
            sheet.submitted_by = request.user
            sheet.submitted_at = timezone.now()
            sheet.returned_comment = ''
            self._transition(sheet, request.user, ModuleResultSheet.Status.SUBMITTED)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='return-for-revision')
    def return_for_revision(self, request, pk=None):
        self._ensure_reviewer()
        sheet = self.get_object()
        if sheet.status not in (ModuleResultSheet.Status.SUBMITTED, ModuleResultSheet.Status.VERIFIED):
            raise ValidationError('Only a SUBMITTED or VERIFIED sheet can be returned.')
        comment = str(request.data.get('comment', '')).strip()
        if not comment:
            raise ValidationError({'comment': ['A return comment is required.']})
        with atomic():
            sheet.revision += 1
            sheet.reviewed_by = request.user
            sheet.reviewed_at = timezone.now()
            sheet.returned_comment = comment
            self._transition(sheet, request.user, ModuleResultSheet.Status.RETURNED, comment)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        self._ensure_reviewer()
        sheet = self.get_object()
        if sheet.status != ModuleResultSheet.Status.SUBMITTED:
            raise ValidationError('Only a SUBMITTED sheet can be verified.')
        with atomic():
            sheet.reviewed_by = request.user
            sheet.reviewed_at = timezone.now()
            self._transition(sheet, request.user, ModuleResultSheet.Status.VERIFIED)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        self._ensure_reviewer()
        sheet = self.get_object()
        if sheet.status != ModuleResultSheet.Status.VERIFIED:
            raise ValidationError('Only a VERIFIED sheet can be locked and published.')
        with atomic():
            sheet.locked_by = request.user
            sheet.locked_at = timezone.now()
            self._transition(sheet, request.user, ModuleResultSheet.Status.LOCKED)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def unlock(self, request, pk=None):
        if not is_admin(request.user):
            raise PermissionDenied('Only an admin may unlock published results.')
        sheet = self.get_object()
        if sheet.status != ModuleResultSheet.Status.LOCKED:
            raise ValidationError('Only a LOCKED sheet can be unlocked.')
        comment = str(request.data.get('comment', '')).strip()
        if not comment:
            raise ValidationError({'comment': ['An unlock reason is required.']})
        with atomic():
            sheet.revision += 1
            sheet.returned_comment = comment
            sheet.locked_by = None
            sheet.locked_at = None
            self._transition(sheet, request.user, ModuleResultSheet.Status.RETURNED, comment)
        return Response(self.get_serializer(self.get_object()).data)


class ModulePeriodResultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ModulePeriodResultSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        queryset = ModulePeriodResult.objects.select_related(
            'sheet__lecture_plan__lecturer', 'enrollment__student'
        )
        if is_admin(user) or is_student_affairs(user):
            pass
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(sheet__lecture_plan__lecturer=user)
        else:
            queryset = queryset.filter(
                enrollment__student=user, sheet__status=ModuleResultSheet.Status.LOCKED
            )
        raw = self.request.query_params.get('sheet_id')
        if raw not in (None, ''):
            queryset = queryset.filter(sheet=uuid_param('sheet_id', raw))
        return queryset
