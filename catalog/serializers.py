from django.db import transaction
from rest_framework import serializers

from catalog.models import (
    Academic,
    AcademicSetupDraft,
    Enrollment,
    Major,
    Module,
    Semester,
    StudentPlacement,
)
from core.exceptions import ConflictError
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


class MajorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Major
        fields = ('id', 'code', 'name', 'duration_years', 'is_active')
        read_only_fields = ('id',)


class StudentPlacementSerializer(serializers.ModelSerializer):
    student_id = serializers.PrimaryKeyRelatedField(
        source='student', queryset=User.objects.filter(role=User.Role.STUDENT)
    )
    semester_id = serializers.PrimaryKeyRelatedField(
        source='semester', queryset=Semester.objects.select_related('academic')
    )
    major_id = serializers.PrimaryKeyRelatedField(
        source='major', queryset=Major.objects.filter(is_active=True)
    )
    student = serializers.SerializerMethodField()
    major = serializers.SerializerMethodField()
    academic_id = serializers.UUIDField(source='semester.academic_id', read_only=True)
    academic_year = serializers.CharField(
        source='semester.academic.academic_year', read_only=True
    )
    semester_no = serializers.IntegerField(source='semester.semester_no', read_only=True)
    semester_name = serializers.CharField(source='semester.semester_name', read_only=True)

    class Meta:
        model = StudentPlacement
        fields = (
            'id', 'student_id', 'student', 'semester_id', 'academic_id', 'academic_year',
            'semester_no', 'semester_name', 'major_id', 'major', 'admitted_on',
            'study_year', 'is_current', 'created_at',
        )
        read_only_fields = (
            'id', 'student', 'academic_id', 'academic_year', 'semester_no',
            'semester_name', 'major', 'created_at',
        )

    def get_student(self, placement):
        return UserBriefSerializer(placement.student).data

    def get_major(self, placement):
        return MajorSerializer(placement.major).data

    def validate(self, attrs):
        major = attrs.get('major') or getattr(self.instance, 'major', None)
        study_year = attrs.get('study_year', getattr(self.instance, 'study_year', 1))
        if major is not None and study_year > major.duration_years:
            raise serializers.ValidationError({
                'study_year': [
                    f'Year {study_year} exceeds the {major.duration_years}-year programme.'
                ]
            })
        return attrs

    def validate_study_year(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('study_year must be from 1 to 5.')
        return value

    @transaction.atomic
    def create(self, validated_data):
        placement = super().create(validated_data)
        if placement.is_current:
            placement.make_current()
        return placement

    @transaction.atomic
    def update(self, instance, validated_data):
        if not instance.is_current:
            raise ConflictError('Historical semester placements are read-only.')
        if 'student' in validated_data and validated_data['student'] != instance.student:
            raise serializers.ValidationError({'student_id': ['student_id cannot be changed.']})
        placement = super().update(instance, validated_data)
        if placement.is_current:
            placement.make_current()
        return placement


class ModuleSerializer(serializers.ModelSerializer):
    semester_id = serializers.PrimaryKeyRelatedField(
        source='semester', queryset=Semester.objects.all()
    )
    academic_id = serializers.UUIDField(source='semester.academic_id', read_only=True)
    academic_year = serializers.CharField(
        source='semester.academic.academic_year', read_only=True
    )
    lecture_plan = serializers.SerializerMethodField()
    eligible_major_ids = serializers.PrimaryKeyRelatedField(
        source='eligible_majors', queryset=Major.objects.filter(is_active=True),
        many=True, required=False,
    )
    eligible_majors = MajorSerializer(many=True, read_only=True)

    class Meta:
        model = Module
        fields = (
            'id', 'semester_id', 'academic_id', 'academic_year',
            'module_code', 'module_name', 'eligible_major_ids',
            'eligible_majors', 'study_year', 'lecture_plan',
        )
        read_only_fields = ('id', 'academic_id', 'academic_year', 'eligible_majors')

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

    def validate(self, attrs):
        semester = attrs.get('semester') or getattr(self.instance, 'semester', None)
        if self.instance is not None and not self.instance.semester.academic.is_current:
            raise ConflictError(
                'Historical module offerings are read-only. Copy the module into the current year.'
            )
        if semester is not None and not semester.academic.is_current:
            raise serializers.ValidationError({
                'semester_id': [
                    'New and edited module offerings must belong to the current academic year.'
                ]
            })
        study_year = attrs.get('study_year', getattr(self.instance, 'study_year', 1))
        majors = attrs.get('eligible_majors')
        if majors is not None:
            invalid = [major.code for major in majors if study_year > major.duration_years]
            if invalid:
                raise serializers.ValidationError({
                    'study_year': [
                        f'Year {study_year} is outside the duration of: {", ".join(invalid)}.'
                    ]
                })
        return attrs

    def validate_study_year(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('study_year must be from 1 to 5.')
        return value


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

    def validate(self, attrs):
        student = attrs.get('student') or getattr(self.instance, 'student', None)
        module = attrs.get('module') or getattr(self.instance, 'module', None)
        if self.instance is not None or student is None or module is None:
            return attrs

        placement = StudentPlacement.objects.filter(
            student=student,
            semester=module.semester,
            study_year=module.study_year,
            is_current=True,
        ).select_related('major').first()
        if placement is None:
            raise serializers.ValidationError({
                'student_id': [
                    'The student is not in this module study year and semester.'
                ]
            })
        eligible = module.eligible_majors.all()
        if eligible.exists() and not eligible.filter(pk=placement.major_id).exists():
            raise serializers.ValidationError({
                'student_id': [
                    f'The module is not available to major {placement.major.code}.'
                ]
            })
        return attrs


class EnrollmentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Enrollment
        fields = ('status',)


class EnrollmentBulkSerializer(serializers.Serializer):
    module_id = serializers.UUIDField()
    student_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class ModuleCopySerializer(serializers.Serializer):
    target_semester_id = serializers.PrimaryKeyRelatedField(
        source='target_semester', queryset=Semester.objects.select_related('academic')
    )


class PlacementPromotionSerializer(serializers.Serializer):
    student_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    target_semester_id = serializers.PrimaryKeyRelatedField(
        source='target_semester', queryset=Semester.objects.select_related('academic')
    )
    major_id = serializers.PrimaryKeyRelatedField(
        source='major', queryset=Major.objects.filter(is_active=True), required=False
    )
    target_study_year = serializers.IntegerField(min_value=1, max_value=5)


class AcademicSetupDraftSerializer(serializers.ModelSerializer):
    created_by = UserBriefSerializer(read_only=True)

    class Meta:
        model = AcademicSetupDraft
        fields = (
            'id', 'name', 'created_by', 'current_step', 'data', 'status',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_by', 'status', 'created_at', 'updated_at')

    def validate_current_step(self, value):
        if value < 1 or value > 4:
            raise serializers.ValidationError('current_step must be from 1 to 4.')
        return value


class SetupAcademicSerializer(serializers.Serializer):
    academic_year = serializers.CharField(max_length=32)
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, attrs):
        require_date_range(attrs['start_date'], attrs['end_date'])
        return attrs


class SetupSemesterSerializer(serializers.Serializer):
    semester_no = serializers.IntegerField(min_value=1, max_value=2)
    semester_name = serializers.CharField(max_length=100)
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, attrs):
        require_date_range(attrs['start_date'], attrs['end_date'])
        return attrs


class SetupMajorSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=150)
    duration_years = serializers.ChoiceField(choices=(4, 5))


class SetupModuleSerializer(serializers.Serializer):
    module_code = serializers.CharField(max_length=32)
    module_name = serializers.CharField(max_length=150)
    study_year = serializers.IntegerField(min_value=1, max_value=5)
    semester_no = serializers.IntegerField(min_value=1, max_value=2)
    major_codes = serializers.ListField(
        child=serializers.CharField(max_length=32), allow_empty=True
    )


class AcademicSetupPublishSerializer(serializers.Serializer):
    academic = SetupAcademicSerializer()
    semesters = SetupSemesterSerializer(many=True, allow_empty=False)
    majors = SetupMajorSerializer(many=True, allow_empty=False)
    modules = SetupModuleSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        semesters = attrs['semesters']
        if sorted(row['semester_no'] for row in semesters) != [1, 2]:
            raise serializers.ValidationError({
                'semesters': ['Exactly Semester 1 and Semester 2 are required.']
            })
        academic = attrs['academic']
        for row in semesters:
            if row['start_date'] < academic['start_date'] or row['end_date'] > academic['end_date']:
                raise serializers.ValidationError({
                    'semesters': [
                        f'Semester {row["semester_no"]} dates must be inside the academic year.'
                    ]
                })
        major_by_code = {row['code'].upper(): row for row in attrs['majors']}
        if len(major_by_code) != len(attrs['majors']):
            raise serializers.ValidationError({'majors': ['Major codes must be unique.']})
        seen_modules = set()
        for index, module in enumerate(attrs['modules']):
            key = (module['study_year'], module['semester_no'], module['module_code'].upper())
            if key in seen_modules:
                raise serializers.ValidationError({
                    'modules': [f'Row {index + 1}: duplicate module in this year and semester.']
                })
            seen_modules.add(key)
            unknown = [code for code in module['major_codes'] if code.upper() not in major_by_code]
            if unknown:
                raise serializers.ValidationError({
                    'modules': [f'Row {index + 1}: unknown majors {", ".join(unknown)}.']
                })
            too_short = [
                code for code in module['major_codes']
                if module['study_year'] > major_by_code[code.upper()]['duration_years']
            ]
            if too_short:
                raise serializers.ValidationError({
                    'modules': [f'Row {index + 1}: study year exceeds {", ".join(too_short)} duration.']
                })
        return attrs
