from django.db import transaction
from rest_framework import serializers

from catalog.models import Academic, Enrollment, Module, Semester
from users.models import User


def require_date_range(start, end):
    if start and end and start >= end:
        raise serializers.ValidationError(
            {'end_date': ['end_date must be after start_date.']}
        )


class AcademicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Academic
        fields = ('id', 'academic_year', 'start_date', 'end_date', 'is_current')
        read_only_fields = ('id',)

    def validate(self, attrs):
        require_date_range(
            attrs.get('start_date', getattr(self.instance, 'start_date', None)),
            attrs.get('end_date', getattr(self.instance, 'end_date', None)),
        )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        academic = super().create(validated_data)
        if academic.is_current:
            academic.clear_other_current()
        return academic

    @transaction.atomic
    def update(self, instance, validated_data):
        academic = super().update(instance, validated_data)
        if academic.is_current:
            academic.clear_other_current()
        return academic


class SemesterSerializer(serializers.ModelSerializer):
    academic_id = serializers.PrimaryKeyRelatedField(
        source='academic', queryset=Academic.objects.all()
    )
    academic_year = serializers.CharField(source='academic.academic_year', read_only=True)

    class Meta:
        model = Semester
        fields = (
            'id', 'academic_id', 'academic_year',
            'semester_no', 'semester_name', 'start_date', 'end_date',
        )
        read_only_fields = ('id', 'academic_year')

    def validate(self, attrs):
        academic = attrs.get('academic') or getattr(self.instance, 'academic', None)
        start = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        end = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        require_date_range(start, end)

        if academic is not None and start is not None and end is not None:
            errors = {}
            if start < academic.start_date:
                errors['start_date'] = [
                    f'Semester starts before academic year {academic.academic_year}.'
                ]
            if end > academic.end_date:
                errors['end_date'] = [
                    f'Semester ends after academic year {academic.academic_year}.'
                ]
            if errors:
                raise serializers.ValidationError(errors)
        return attrs


class ModuleSerializer(serializers.ModelSerializer):
    semester_id = serializers.PrimaryKeyRelatedField(
        source='semester', queryset=Semester.objects.all()
    )
    academic_id = serializers.UUIDField(source='semester.academic_id', read_only=True)
    academic_year = serializers.CharField(
        source='semester.academic.academic_year', read_only=True
    )
    lecture_plan = serializers.SerializerMethodField()

    class Meta:
        model = Module
        fields = (
            'id', 'semester_id', 'academic_id', 'academic_year',
            'module_code', 'module_name', 'credit_hours', 'lecture_plan',
        )
        read_only_fields = ('id', 'academic_id', 'academic_year')

    def get_lecture_plan(self, module):
        """Who the module was offered to, or None while the admin has not assigned it yet."""
        plan = getattr(module, 'lecture_plan', None)
        if plan is None:
            return None
        return {
            'id': str(plan.id),
            'status': plan.status,
            'lecturer': UserBriefSerializer(plan.lecturer).data,
        }


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'full_name', 'email')
        read_only_fields = fields


class ModuleBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = ('id', 'module_code', 'module_name')
        read_only_fields = fields


class EnrollmentSerializer(serializers.ModelSerializer):
    student_id = serializers.PrimaryKeyRelatedField(
        source='student', queryset=User.objects.filter(role=User.Role.STUDENT)
    )
    module_id = serializers.PrimaryKeyRelatedField(
        source='module', queryset=Module.objects.all()
    )
    student = UserBriefSerializer(read_only=True)
    module = ModuleBriefSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = ('id', 'student_id', 'module_id', 'student', 'module', 'status', 'enrolled_at')
        read_only_fields = ('id', 'status', 'enrolled_at')


class EnrollmentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Enrollment
        fields = ('status',)


class EnrollmentBulkSerializer(serializers.Serializer):
    module_id = serializers.UUIDField()
    student_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
