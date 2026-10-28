from django.db.models import Sum
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from catalog.models import Module, Semester
from catalog.serializers import UserBriefSerializer
from core.exceptions import ConflictError
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from teaching.permissions import is_admin, owns_plan
from users.models import User


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


class PlanSemesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Semester
        fields = ('id', 'semester_no', 'semester_name')
        read_only_fields = fields


class LecturePlanSerializer(serializers.ModelSerializer):
    module_id = serializers.PrimaryKeyRelatedField(
        source='module', queryset=Module.objects.all(), required=False
    )
    lecturer_id = serializers.PrimaryKeyRelatedField(
        source='lecturer', queryset=User.objects.all(), required=False
    )
    module = PlanModuleSerializer(read_only=True)
    semester = PlanSemesterSerializer(source='module.semester', read_only=True)
    lecturer = UserBriefSerializer(read_only=True)
    academic_id = serializers.UUIDField(source='academic.id', read_only=True)
    academic_year = serializers.CharField(source='academic.academic_year', read_only=True)
    assessment_total = serializers.SerializerMethodField()
    total_marks = serializers.SerializerMethodField()

    class Meta:
        model = LecturePlan
        fields = (
            'id', 'module_id', 'module', 'semester', 'lecturer_id', 'lecturer',
            'academic_id', 'academic_year', 'note', 'status',
            'assessment_total', 'total_marks',
        )
        read_only_fields = ('id', 'status', 'academic_id', 'academic_year')

    def get_assessment_total(self, plan):
        count = getattr(plan, 'assessment_total', None)
        if count is None:
            count = Assessment.objects.filter(distribution__lecture_plan=plan).count()
        return count

    def get_total_marks(self, plan):
        total = getattr(plan, 'total_marks', None)
        if total is None:
            total = Assessment.objects.filter(distribution__lecture_plan=plan).aggregate(
                total=Sum('max_mark')
            )['total']
        return f'{total or 0:.2f}'

    def validate(self, attrs):
        request = self.context['request']

        if self.instance is None:
            if not is_admin(request.user):
                raise PermissionDenied('Only an admin may offer a module to a teacher.')

            module = attrs.get('module')
            if module is None:
                raise serializers.ValidationError({'module_id': ['This field is required.']})
            if LecturePlan.objects.filter(module=module).exists():
                raise ConflictError(
                    'This module already has a lecture plan.',
                    fields={'module_id': ['A module has at most one lecture plan.']},
                )

            academic = module.semester.academic
            if not academic.is_current:
                raise serializers.ValidationError(
                    {
                        'module_id': [
                            f'{module.module_code} belongs to academic year '
                            f'{academic.academic_year}, which is not the current year.'
                        ]
                    }
                )

            lecturer = attrs.get('lecturer')
            if lecturer is None:
                raise serializers.ValidationError(
                    {'lecturer_id': ['A lecturer with role TEACHER is required.']}
                )
            if lecturer.role != User.Role.TEACHER:
                raise serializers.ValidationError(
                    {'lecturer_id': ['The lecturer must have role TEACHER.']}
                )

            attrs['academic'] = academic
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
    total_marks = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentDistribution
        fields = (
            'id', 'lecture_plan_id', 'assessment_type',
            'total_assessment', 'display_order', 'status', 'assessment_count', 'total_marks',
        )
        read_only_fields = ('id', 'status')

    def get_assessment_count(self, distribution):
        count = getattr(distribution, 'assessment_count', None)
        return distribution.assessments.count() if count is None else count

    def get_total_marks(self, distribution):
        total = getattr(distribution, 'total_marks', None)
        if total is None:
            total = distribution.assessments.aggregate(total=Sum('max_mark'))['total']
        return f'{total or 0:.2f}'

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
