from django.db.models import Sum
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from catalog.models import Module
from catalog.serializers import UserBriefSerializer
from core.exceptions import ConflictError
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from teaching.permissions import owns_plan
from users.models import User


def is_teacher(user):
    return user.role == User.Role.TEACHER


def ensure_draft(plan):
    if plan.status != LecturePlan.Status.DRAFT:
        raise ConflictError(
            f'The lecture plan is {plan.status}. Revert it to DRAFT before writing this row.'
        )


class PlanModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = ('id', 'module_code', 'module_name', 'credit_hours')
        read_only_fields = fields


class LecturePlanSerializer(serializers.ModelSerializer):
    module_id = serializers.PrimaryKeyRelatedField(
        source='module', queryset=Module.objects.all(), required=False
    )
    lecturer_id = serializers.PrimaryKeyRelatedField(
        source='lecturer', queryset=User.objects.all(), required=False
    )
    module = PlanModuleSerializer(read_only=True)
    lecturer = UserBriefSerializer(read_only=True)
    academic_id = serializers.UUIDField(source='academic.id', read_only=True)
    academic_year = serializers.CharField(source='academic.academic_year', read_only=True)
    percentage_total = serializers.SerializerMethodField()

    class Meta:
        model = LecturePlan
        fields = (
            'id', 'module_id', 'module', 'lecturer_id', 'lecturer',
            'academic_id', 'academic_year', 'note', 'status', 'percentage_total',
        )
        read_only_fields = ('id', 'status', 'academic_id', 'academic_year')

    def get_percentage_total(self, plan):
        total = getattr(plan, 'percentage_total', None)
        if total is None:
            total = plan.distributions.aggregate(total=Sum('percentage'))['total']
        return total or 0

    def validate(self, attrs):
        request = self.context['request']

        if self.instance is None:
            module = attrs.get('module')
            if module is None:
                raise serializers.ValidationError({'module_id': ['This field is required.']})
            if LecturePlan.objects.filter(module=module).exists():
                raise ConflictError(
                    'This module already has a lecture plan.',
                    fields={'module_id': ['A module has at most one lecture plan.']},
                )
            attrs['academic'] = module.semester.academic

            lecturer = attrs.get('lecturer')
            if lecturer is None:
                if not is_teacher(request.user):
                    raise serializers.ValidationError(
                        {'lecturer_id': ['A lecturer with role TEACHER is required.']}
                    )
                lecturer = request.user
            elif lecturer.role != User.Role.TEACHER:
                raise serializers.ValidationError(
                    {'lecturer_id': ['The lecturer must have role TEACHER.']}
                )
            if is_teacher(request.user) and lecturer.pk != request.user.pk:
                raise serializers.ValidationError(
                    {'lecturer_id': ['A teacher may only plan their own module.']}
                )
            attrs['lecturer'] = lecturer
            return attrs

        if 'module' in attrs and attrs['module'] != self.instance.module:
            raise serializers.ValidationError({'module_id': ['module_id cannot be changed.']})
        if 'lecturer' in attrs and attrs['lecturer'] != self.instance.lecturer:
            if request.user.role != User.Role.ADMIN and not request.user.is_superuser:
                raise serializers.ValidationError(
                    {'lecturer_id': ['Only an admin may change the lecturer.']}
                )
            if attrs['lecturer'].role != User.Role.TEACHER:
                raise serializers.ValidationError(
                    {'lecturer_id': ['The lecturer must have role TEACHER.']}
                )
            if self.instance.status != LecturePlan.Status.DRAFT:
                raise ConflictError('The lecturer is frozen once the plan is published.')
        return attrs


class AssessmentDistributionSerializer(serializers.ModelSerializer):
    lecture_plan_id = serializers.PrimaryKeyRelatedField(
        source='lecture_plan', queryset=LecturePlan.objects.all()
    )
    assessment_count = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentDistribution
        fields = (
            'id', 'lecture_plan_id', 'assessment_type', 'percentage',
            'total_assessment', 'display_order', 'status', 'assessment_count',
        )
        read_only_fields = ('id', 'status')

    def get_assessment_count(self, distribution):
        count = getattr(distribution, 'assessment_count', None)
        return distribution.assessments.count() if count is None else count

    def validate(self, attrs):
        plan = attrs.get('lecture_plan') or self.instance.lecture_plan
        if self.instance is not None and 'lecture_plan' in attrs:
            if attrs['lecture_plan'] != self.instance.lecture_plan:
                raise serializers.ValidationError(
                    {'lecture_plan_id': ['lecture_plan_id cannot be changed.']}
                )
        if self.instance is None and not owns_plan(self.context['request'].user, plan):
            raise PermissionDenied('Only an admin or the plan owner may add a distribution.')
        ensure_draft(plan)
        return attrs


class AssessmentSerializer(serializers.ModelSerializer):
    assessment_distribution_id = serializers.PrimaryKeyRelatedField(
        source='distribution', queryset=AssessmentDistribution.objects.all()
    )
    lecture_plan_id = serializers.UUIDField(
        source='distribution.lecture_plan_id', read_only=True
    )
    module_id = serializers.UUIDField(
        source='distribution.lecture_plan.module_id', read_only=True
    )
    assessment_type = serializers.CharField(source='distribution.assessment_type', read_only=True)
    distribution_status = serializers.CharField(source='distribution.status', read_only=True)

    class Meta:
        model = Assessment
        fields = (
            'id', 'assessment_distribution_id', 'lecture_plan_id', 'module_id',
            'assessment_type', 'title', 'sequence_no', 'max_mark', 'assessment_date',
            'due_at', 'status', 'distribution_status',
        )
        read_only_fields = ('id', 'status')

    def validate(self, attrs):
        if self.instance is None:
            plan = attrs['distribution'].lecture_plan
            if not owns_plan(self.context['request'].user, plan):
                raise PermissionDenied('Only an admin or the plan owner may add an assessment.')
            ensure_draft(plan)
            return attrs

        plan = self.instance.distribution.lecture_plan
        changed = {
            field
            for field, value in attrs.items()
            if field != 'distribution' and getattr(self.instance, field) != value
        }
        reparented = attrs.get('distribution', self.instance.distribution) != self.instance.distribution

        if plan.status == LecturePlan.Status.DRAFT:
            if reparented:
                raise serializers.ValidationError(
                    {'assessment_distribution_id': ['The parent distribution cannot be changed.']}
                )
            return attrs

        if reparented or changed & {'max_mark', 'sequence_no'}:
            raise ConflictError(
                'The plan is published. max_mark, sequence_no and the parent distribution are frozen.'
            )
        if changed and self.instance.results.exists():
            raise ConflictError('Marks already exist for this assessment. Its details are frozen.')
        return attrs
