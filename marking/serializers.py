from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from catalog.models import Enrollment
from marking.models import (
    AssessmentResult,
    ModulePeriodResult,
    ModuleResultSheet,
    ResultWorkflowEvent,
)
from marking.services import percentage_of, record_result, validate_mark
from teaching.models import Assessment
from teaching.permissions import owns_plan
from users.models import User


class GradedBySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'full_name')
        read_only_fields = fields


class AssessmentResultSerializer(serializers.ModelSerializer):
    assessment_id = serializers.PrimaryKeyRelatedField(
        source='assessment',
        queryset=Assessment.objects.select_related('distribution__lecture_plan__module'),
    )
    enrollment_id = serializers.PrimaryKeyRelatedField(
        source='enrollment', queryset=Enrollment.objects.select_related('module')
    )
    marks_obtained = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)
    graded_by = GradedBySerializer(read_only=True)
    graded_at = serializers.DateTimeField(read_only=True)
    max_mark = serializers.DecimalField(
        source='assessment.max_mark', max_digits=6, decimal_places=2, read_only=True
    )
    percentage = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentResult
        fields = (
            'id', 'assessment_id', 'enrollment_id', 'marks_obtained', 'remark',
            'graded_by', 'graded_at', 'max_mark', 'percentage',
        )
        read_only_fields = ('id',)

    def get_percentage(self, result):
        return percentage_of(result.marks_obtained, result.assessment.max_mark)

    def validate(self, attrs):
        assessment = attrs.get('assessment') or getattr(self.instance, 'assessment', None)
        enrollment = attrs.get('enrollment') or getattr(self.instance, 'enrollment', None)
        marks = attrs.get('marks_obtained', getattr(self.instance, 'marks_obtained', None))
        validate_mark(self.context['request'].user, assessment, enrollment, marks)
        return attrs

    def create(self, validated_data):
        return AssessmentResult.objects.create(
            graded_by=self.context['request'].user,
            graded_at=timezone.now(),
            **validated_data,
        )

    def update(self, instance, validated_data):
        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
        instance.graded_by = self.context['request'].user
        instance.graded_at = timezone.now()
        instance.save()
        return instance


class AssessmentResultBulkRowSerializer(serializers.Serializer):
    enrollment_id = serializers.UUIDField()
    marks_obtained = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)
    remark = serializers.CharField(required=False, allow_blank=True, default='')


class AssessmentResultBulkSerializer(serializers.Serializer):
    assessment_id = serializers.UUIDField()
    results = AssessmentResultBulkRowSerializer(many=True)

    def validate(self, attrs):
        assessment = Assessment.objects.select_related(
            'distribution__lecture_plan__module'
        ).filter(pk=attrs['assessment_id']).first()
        if assessment is None:
            raise serializers.ValidationError({'assessment_id': ['Assessment not found.']})

        user = self.context['request'].user
        if not owns_plan(user, assessment.lecture_plan):
            raise PermissionDenied('Only the owning teacher or an admin may record marks.')

        rows = attrs['results']
        ids = [row['enrollment_id'] for row in rows]
        found = {
            enrollment.pk: enrollment
            for enrollment in Enrollment.objects.select_related('module').filter(pk__in=ids)
        }
        errors = [
            f'Row {index}: unknown enrollment {row["enrollment_id"]}.'
            for index, row in enumerate(rows)
            if row['enrollment_id'] not in found
        ]
        errors += [
            f'Enrollment {pk} appears more than once.'
            for pk in sorted({str(pk) for pk in ids if ids.count(pk) > 1})
        ]
        if errors:
            raise serializers.ValidationError({'results': errors})

        for index, row in enumerate(rows):
            try:
                validate_mark(user, assessment, found[row['enrollment_id']], row['marks_obtained'])
            except serializers.ValidationError as exc:
                raise serializers.ValidationError(
                    {'results': [f'Row {index}: {line}' for line in self._lines(exc)]}
                )

        attrs['assessment'] = assessment
        attrs['enrollments'] = found
        return attrs

    @staticmethod
    def _lines(exc):
        detail = getattr(exc, 'detail', str(exc))
        if isinstance(detail, dict):
            return [str(line) for values in detail.values() for line in values]
        if isinstance(detail, list):
            return [str(line) for line in detail]
        return [str(detail)]

    def save(self):
        assessment = self.validated_data['assessment']
        user = self.context['request'].user
        return [
            record_result(
                assessment,
                self.validated_data['enrollments'][row['enrollment_id']],
                row['marks_obtained'],
                row['remark'],
                user,
            )
            for row in self.validated_data['results']
        ]


class ResultWorkflowEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.full_name', read_only=True)

    class Meta:
        model = ResultWorkflowEvent
        fields = ('id', 'actor_name', 'from_status', 'to_status', 'comment', 'created_at')


class ModulePeriodResultSerializer(serializers.ModelSerializer):
    enrollment_id = serializers.UUIDField(source='enrollment.id', read_only=True)
    student_id = serializers.UUIDField(source='enrollment.student.id', read_only=True)
    student_name = serializers.CharField(source='enrollment.student.full_name', read_only=True)
    student_number = serializers.CharField(source='enrollment.student.username', read_only=True)

    class Meta:
        model = ModulePeriodResult
        fields = (
            'id', 'enrollment_id', 'student_id', 'student_name', 'student_number',
            'raw_mark', 'maximum_mark', 'remark', 'source', 'entered_at',
        )


class ModuleResultSheetSerializer(serializers.ModelSerializer):
    module_id = serializers.UUIDField(source='lecture_plan.module.id', read_only=True)
    module_code = serializers.CharField(source='lecture_plan.module.module_code', read_only=True)
    module_name = serializers.CharField(source='lecture_plan.module.module_name', read_only=True)
    semester_id = serializers.UUIDField(source='lecture_plan.module.semester.id', read_only=True)
    semester_name = serializers.CharField(
        source='lecture_plan.module.semester.semester_name', read_only=True
    )
    academic_id = serializers.UUIDField(source='lecture_plan.academic.id', read_only=True)
    academic_year = serializers.CharField(
        source='lecture_plan.academic.academic_year', read_only=True
    )
    lecturer_id = serializers.UUIDField(source='lecture_plan.lecturer.id', read_only=True)
    lecturer_name = serializers.CharField(source='lecture_plan.lecturer.full_name', read_only=True)
    enrolled_count = serializers.IntegerField(read_only=True)
    marked_count = serializers.IntegerField(read_only=True)
    results = ModulePeriodResultSerializer(many=True, read_only=True)
    workflow_events = ResultWorkflowEventSerializer(many=True, read_only=True)
    roster = serializers.SerializerMethodField()

    def get_fields(self):
        fields = super().get_fields()
        view = self.context.get('view')
        if view is not None and view.action == 'list':
            for name in ('results', 'roster', 'workflow_events'):
                fields.pop(name, None)
        return fields

    def get_roster(self, sheet):
        view = self.context.get('view')
        if view is not None and view.action == 'list':
            return []
        existing = {row.enrollment_id: row for row in sheet.results.all()}
        enrollments = Enrollment.objects.select_related('student').filter(
            module=sheet.lecture_plan.module,
            status__in=(Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED),
        ).order_by('student__username', 'student__full_name')
        return [
            {
                'enrollment_id': str(enrollment.pk),
                'student_id': str(enrollment.student_id),
                'student_name': enrollment.student.full_name,
                'student_number': enrollment.student.username,
                'raw_mark': str(existing[enrollment.pk].raw_mark) if enrollment.pk in existing else '',
                'maximum_mark': (
                    str(existing[enrollment.pk].maximum_mark)
                    if enrollment.pk in existing and existing[enrollment.pk].maximum_mark is not None
                    else None
                ),
                'remark': existing[enrollment.pk].remark if enrollment.pk in existing else '',
            }
            for enrollment in enrollments
        ]

    class Meta:
        model = ModuleResultSheet
        fields = (
            'id', 'module_id', 'module_code', 'module_name', 'semester_id',
            'semester_name', 'academic_id', 'academic_year', 'lecturer_id',
            'lecturer_name', 'status', 'revision', 'enrolled_count', 'marked_count',
            'submitted_at', 'reviewed_at', 'returned_comment', 'locked_at',
            'updated_at', 'results', 'roster', 'workflow_events',
        )


class ModulePeriodResultBulkRowSerializer(serializers.Serializer):
    enrollment_id = serializers.UUIDField()
    raw_mark = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)
    maximum_mark = serializers.DecimalField(
        max_digits=7, decimal_places=2, min_value=0, required=False, allow_null=True
    )
    remark = serializers.CharField(required=False, allow_blank=True, default='')


class ModulePeriodResultBulkSerializer(serializers.Serializer):
    results = ModulePeriodResultBulkRowSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        sheet = self.context['sheet']
        if sheet.status not in (ModuleResultSheet.Status.DRAFT, ModuleResultSheet.Status.RETURNED):
            raise serializers.ValidationError('Marks can only be edited in DRAFT or RETURNED state.')
        rows = attrs['results']
        ids = [row['enrollment_id'] for row in rows]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({'results': ['An enrollment appears more than once.']})
        enrollments = {
            row.pk: row for row in Enrollment.objects.filter(
                pk__in=ids, module=sheet.lecture_plan.module,
                status__in=(Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED),
            )
        }
        missing = [str(pk) for pk in ids if pk not in enrollments]
        if missing:
            raise serializers.ValidationError(
                {'results': [f'Invalid enrollment for this module: {pk}' for pk in missing]}
            )
        for row in rows:
            maximum = row.get('maximum_mark')
            if maximum is not None and row['raw_mark'] > maximum:
                raise serializers.ValidationError(
                    {'results': [f'Raw mark cannot exceed maximum mark for {row["enrollment_id"]}.']}
                )
        attrs['enrollments'] = enrollments
        return attrs

    def save(self):
        sheet = self.context['sheet']
        actor = self.context['request'].user
        saved = []
        for row in self.validated_data['results']:
            result, _ = ModulePeriodResult.objects.update_or_create(
                sheet=sheet,
                enrollment=self.validated_data['enrollments'][row['enrollment_id']],
                defaults={
                    'raw_mark': row['raw_mark'],
                    'maximum_mark': row.get('maximum_mark'),
                    'remark': row['remark'],
                    'entered_by': actor,
                    'entered_at': timezone.now(),
                },
            )
            saved.append(result)
        return saved
