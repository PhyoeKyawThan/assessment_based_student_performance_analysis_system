from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.db.transaction import atomic
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from core.exceptions import ConflictError
from core.filters import uuid_param
from core.views import RepresentationMixin
from marking import services
from marking.models import AssessmentResult
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from teaching.notifications import notify_assessment_opened
from teaching.permissions import IsPlanWriter, is_admin
from teaching.serializers import (
    AssessmentDistributionSerializer,
    AssessmentSerializer,
    LecturePlanSerializer,
)
from users.models import User
from users.permissions import IsLecturerRole


def readable_plan_filter(prefix, user):
    """Limit a lecture-plan-rooted queryset to the rows this role may read."""
    if is_admin(user):
        return Q()
    if user.role == User.Role.TEACHER:
        return Q(**{f'{prefix}lecturer': user})
    return Q(
        **{
            f'{prefix}status': LecturePlan.Status.PUBLISHED,
            f'{prefix}module__enrollments__student': user,
        }
    )


class LecturePlanViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = LecturePlan.objects.select_related(
        'module__semester__academic', 'lecturer'
    ).annotate(percentage_total=Coalesce(Sum('distributions__percentage'), 0)).order_by(
        'module__module_code'
    )
    serializer_class = LecturePlanSerializer
    representation_serializer_class = LecturePlanSerializer
    permission_classes = (IsPlanWriter,)

    def get_queryset(self):
        queryset = super().get_queryset().filter(readable_plan_filter('', self.request.user))
        params = self.request.query_params

        for param, lookup in (
            ('module_id', 'module'),
            ('lecturer_id', 'lecturer'),
            ('academic_id', 'academic'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def perform_destroy(self, instance):
        if instance.status != LecturePlan.Status.DRAFT:
            raise ConflictError('Only a DRAFT lecture plan can be deleted.')
        with atomic():
            Assessment.objects.filter(distribution__lecture_plan=instance).delete()
            instance.distributions.all().delete()
            instance.delete()

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        plan = self.get_object()
        if plan.status == LecturePlan.Status.PUBLISHED:
            raise ConflictError('This lecture plan is already published.')

        errors = plan.publish_errors()
        if errors:
            raise ConflictError('The plan is not complete enough to publish.', fields=errors)

        plan.status = LecturePlan.Status.PUBLISHED
        plan.save(update_fields=['status'])
        return Response(self.representation_serializer_class(plan).data)

    @action(detail=True, methods=['post'], url_path='revert-to-draft')
    def revert_to_draft(self, request, pk=None):
        plan = self.get_object()
        if plan.status == LecturePlan.Status.DRAFT:
            raise ConflictError('This lecture plan is already DRAFT.')
        if AssessmentResult.objects.filter(
            assessment__distribution__lecture_plan=plan
        ).exists():
            raise ConflictError('Marks already exist for this plan. It cannot go back to DRAFT.')

        plan.revert_to_draft()
        return Response(self.representation_serializer_class(plan).data)


class AssessmentDistributionViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = AssessmentDistribution.objects.select_related(
        'lecture_plan'
    ).annotate(assessment_count=Count('assessments')).order_by('lecture_plan', 'display_order')
    serializer_class = AssessmentDistributionSerializer
    representation_serializer_class = AssessmentDistributionSerializer
    permission_classes = (IsPlanWriter,)

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            readable_plan_filter('lecture_plan__', self.request.user)
        )
        raw = self.request.query_params.get('lecture_plan_id')
        if raw not in (None, ''):
            queryset = queryset.filter(lecture_plan=uuid_param('lecture_plan_id', raw))
        return queryset

    def perform_destroy(self, instance):
        if instance.lecture_plan.status != LecturePlan.Status.DRAFT:
            raise ConflictError('Only a DRAFT plan allows distribution deletes.')
        if instance.assessments.filter(results__isnull=False).exists():
            raise ConflictError('An assessment in this distribution already has marks.')
        with atomic():
            instance.assessments.all().delete()
            instance.delete()


class AssessmentViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Assessment.objects.select_related(
        'distribution__lecture_plan__module'
    )
    serializer_class = AssessmentSerializer
    representation_serializer_class = AssessmentSerializer
    permission_classes = (IsPlanWriter,)

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            readable_plan_filter('distribution__lecture_plan__', self.request.user)
        )
        params = self.request.query_params

        for param, lookup in (
            ('assessment_distribution_id', 'distribution'),
            ('lecture_plan_id', 'distribution__lecture_plan'),
            ('module_id', 'distribution__lecture_plan__module'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def perform_destroy(self, instance):
        if instance.lecture_plan.status != LecturePlan.Status.DRAFT:
            raise ConflictError('Only a DRAFT plan allows assessment deletes.')
        if instance.results.exists():
            raise ConflictError('This assessment already has marks.')
        instance.delete()

    @action(detail=True, methods=['post'])
    def open(self, request, pk=None):
        assessment = self.get_object()
        if assessment.lecture_plan.status != LecturePlan.Status.PUBLISHED:
            raise ConflictError('The lecture plan must be PUBLISHED before opening an assessment.')
        if assessment.status != Assessment.Status.SCHEDULED:
            raise ConflictError(f'Cannot open an assessment that is {assessment.status}.')
        assessment.status = Assessment.Status.OPEN
        assessment.save(update_fields=['status'])
        notify_assessment_opened(assessment)
        return Response(self.representation_serializer_class(assessment).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        assessment = self.get_object()
        if assessment.status != Assessment.Status.OPEN:
            raise ConflictError(f'Only an OPEN assessment can be closed. It is {assessment.status}.')
        assessment.status = Assessment.Status.CLOSED
        assessment.save(update_fields=['status'])
        return Response(self.representation_serializer_class(assessment).data)

    @action(detail=True, methods=['post'], url_path='mark-graded')
    def mark_graded(self, request, pk=None):
        assessment = self.get_object()
        services.mark_graded(assessment)
        data = self.representation_serializer_class(assessment).data
        data['run_finished'] = assessment.lecture_plan.is_run_finished()
        return Response(data)


class MyLecturePlansView(ListAPIView):
    permission_classes = (IsLecturerRole,)
    serializer_class = LecturePlanSerializer

    def get_queryset(self):
        queryset = LecturePlan.objects.select_related(
            'module__semester__academic', 'lecturer'
        ).filter(lecturer=self.request.user)
        params = self.request.query_params

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        raw = params.get('academic_id')
        if raw not in (None, ''):
            queryset = queryset.filter(academic=uuid_param('academic_id', raw))
        return queryset
