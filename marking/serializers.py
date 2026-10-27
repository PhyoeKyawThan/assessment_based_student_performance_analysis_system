from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from catalog.models import Enrollment
from marking.models import AssessmentResult
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
