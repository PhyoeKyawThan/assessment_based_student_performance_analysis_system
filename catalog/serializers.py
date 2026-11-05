from django.db import transaction
from datetime import date
from rest_framework import serializers

from catalog.models import (
    Academic,
    AcademicSetupDraft,
    Cohort,
    CohortTerm,
    CurriculumModule,
    CurriculumVersion,
    Enrollment,
    Major,
    Module,
    ModuleDefinition,
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
            'semester_no', 'semester_name', 'start_date', 'end_date', 'status',
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


class TeachingPeriodSerializer(serializers.ModelSerializer):
    """Public replacement for the old Academic -> Semester setup flow."""

    academic_label = serializers.CharField(source='academic.academic_year')
    period_number = serializers.IntegerField(source='semester_no')
    name = serializers.CharField(source='semester_name')

    class Meta:
        model = Semester
        fields = (
            'id', 'academic_label', 'period_number', 'name',
            'start_date', 'end_date', 'status',
        )
        read_only_fields = ('id',)

    def validate_period_number(self, value):
        if value not in (1, 2):
            raise serializers.ValidationError('Use period 1 or period 2.')
        return value

    def validate(self, attrs):
        require_date_range(
            attrs.get('start_date', getattr(self.instance, 'start_date', None)),
            attrs.get('end_date', getattr(self.instance, 'end_date', None)),
        )
        return attrs

    @staticmethod
    def _sync_academic_dates(academic):
        periods = academic.semesters.order_by('start_date')
        if periods.exists():
            academic.start_date = periods.first().start_date
            academic.end_date = periods.order_by('-end_date').first().end_date
            academic.save(update_fields=['start_date', 'end_date'])

    @transaction.atomic
    def create(self, validated_data):
        academic_data = validated_data.pop('academic')
        label = academic_data['academic_year'].strip()
        start = validated_data['start_date']
        end = validated_data['end_date']
        academic, _created = Academic.objects.get_or_create(
            academic_year=label,
            defaults={'start_date': start, 'end_date': end, 'is_current': False},
        )
        period = Semester.objects.create(academic=academic, **validated_data)
        self._sync_academic_dates(academic)
        return period

    @transaction.atomic
    def update(self, instance, validated_data):
        old_academic = instance.academic
        academic_data = validated_data.pop('academic', None)
        if academic_data is not None:
            label = academic_data['academic_year'].strip()
            academic, _created = Academic.objects.get_or_create(
                academic_year=label,
                defaults={
                    'start_date': validated_data.get('start_date', instance.start_date),
                    'end_date': validated_data.get('end_date', instance.end_date),
                    'is_current': False,
                },
            )
            instance.academic = academic
        period = super().update(instance, validated_data)
        self._sync_academic_dates(old_academic)
        self._sync_academic_dates(period.academic)
        return period


class MajorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Major
        fields = ('id', 'code', 'name', 'duration_years', 'is_active')
        read_only_fields = ('id',)


class ModuleDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModuleDefinition
        fields = ('id', 'code', 'name', 'is_active', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_code(self, value):
        return value.strip().upper()


class CurriculumModuleSerializer(serializers.ModelSerializer):
    curriculum_id = serializers.PrimaryKeyRelatedField(
        source='curriculum', queryset=CurriculumVersion.objects.select_related('major')
    )
    module_definition_id = serializers.PrimaryKeyRelatedField(
        source='module_definition', queryset=ModuleDefinition.objects.filter(is_active=True)
    )
    module_code = serializers.CharField(source='module_definition.code', read_only=True)
    module_name = serializers.CharField(source='module_definition.name', read_only=True)
    semester_number = serializers.IntegerField(min_value=1, max_value=10, required=False)
    study_year = serializers.IntegerField(min_value=1, max_value=5, required=False)
    term_number = serializers.IntegerField(min_value=1, max_value=2, required=False)

    class Meta:
        model = CurriculumModule
        fields = (
            'id', 'curriculum_id', 'module_definition_id', 'module_code', 'module_name',
            'semester_number', 'study_year', 'term_number',
        )
        read_only_fields = ('id', 'module_code', 'module_name')
        # The model's legacy unique constraint mentions study_year/term_number.
        # They are derived from semester_number after field validation, so the
        # automatic validator must not require clients to submit both forms.
        validators = []

    def validate(self, attrs):
        curriculum = attrs.get('curriculum') or getattr(self.instance, 'curriculum', None)
        semester_number = attrs.pop('semester_number', None)
        if semester_number is not None:
            attrs['study_year'] = ((semester_number - 1) // 2) + 1
            attrs['term_number'] = 1 if semester_number % 2 else 2
        elif self.instance is None and (
            'study_year' not in attrs or 'term_number' not in attrs
        ):
            raise serializers.ValidationError({
                'semester_number': ['Choose the fixed programme semester.']
            })
        study_year = attrs.get('study_year', getattr(self.instance, 'study_year', 1))
        if curriculum is not None and study_year > curriculum.major.duration_years:
            raise serializers.ValidationError({
                'study_year': ['Study year exceeds this programme duration.']
            })
        term = attrs.get('term_number', getattr(self.instance, 'term_number', 1))
        if term not in (1, 2):
            raise serializers.ValidationError({'term_number': ['Use Term 1 or Term 2.']})
        definition = attrs.get('module_definition') or getattr(
            self.instance, 'module_definition', None
        )
        if curriculum is not None and definition is not None:
            duplicate = CurriculumModule.objects.filter(
                curriculum=curriculum,
                module_definition=definition,
                study_year=study_year,
                term_number=term,
            )
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError({
                    'module_definition_id': ['This module is already in that semester.']
                })
        return attrs


class CurriculumVersionSerializer(serializers.ModelSerializer):
    major_id = serializers.PrimaryKeyRelatedField(
        source='major', queryset=Major.objects.filter(is_active=True)
    )
    major_code = serializers.CharField(source='major.code', read_only=True)
    effective_from_id = serializers.PrimaryKeyRelatedField(
        source='effective_from', queryset=Academic.objects.all(), required=False, allow_null=True
    )
    module_slots = CurriculumModuleSerializer(many=True, read_only=True)

    class Meta:
        model = CurriculumVersion
        fields = (
            'id', 'major_id', 'major_code', 'name', 'effective_from_id',
            'status', 'created_at', 'module_slots',
        )
        read_only_fields = ('id', 'major_code', 'created_at', 'module_slots')

    @transaction.atomic
    def create(self, validated_data):
        curriculum = super().create(validated_data)
        if curriculum.status == CurriculumVersion.Status.ACTIVE:
            CurriculumVersion.objects.filter(
                major=curriculum.major, status=CurriculumVersion.Status.ACTIVE
            ).exclude(pk=curriculum.pk).update(status=CurriculumVersion.Status.RETIRED)
        return curriculum

    @transaction.atomic
    def update(self, instance, validated_data):
        curriculum = super().update(instance, validated_data)
        if curriculum.status == CurriculumVersion.Status.ACTIVE:
            CurriculumVersion.objects.filter(
                major=curriculum.major, status=CurriculumVersion.Status.ACTIVE
            ).exclude(pk=curriculum.pk).update(status=CurriculumVersion.Status.RETIRED)
        return curriculum


class CohortSerializer(serializers.ModelSerializer):
    major_id = serializers.PrimaryKeyRelatedField(
        source='major', queryset=Major.objects.filter(is_active=True)
    )
    curriculum_id = serializers.PrimaryKeyRelatedField(
        source='curriculum', queryset=CurriculumVersion.objects.all()
    )
    major_code = serializers.CharField(source='major.code', read_only=True)

    class Meta:
        model = Cohort
        fields = (
            'id', 'name', 'admission_year', 'major_id', 'major_code',
            'curriculum_id', 'is_active',
        )
        read_only_fields = ('id', 'major_code')

    def validate(self, attrs):
        major = attrs.get('major') or getattr(self.instance, 'major', None)
        curriculum = attrs.get('curriculum') or getattr(self.instance, 'curriculum', None)
        if major is not None and curriculum is not None and curriculum.major_id != major.pk:
            raise serializers.ValidationError({'curriculum': ['Curriculum must belong to the cohort major.']})
        return attrs


class CohortTermSerializer(serializers.ModelSerializer):
    cohort_id = serializers.PrimaryKeyRelatedField(
        source='cohort', queryset=Cohort.objects.select_related('major')
    )
    semester_id = serializers.PrimaryKeyRelatedField(
        source='semester', queryset=Semester.objects.select_related('academic')
    )

    class Meta:
        model = CohortTerm
        fields = ('id', 'cohort_id', 'semester_id', 'study_year', 'programme_semester')
        read_only_fields = ('id', 'programme_semester')

    def validate(self, attrs):
        cohort = attrs.get('cohort') or getattr(self.instance, 'cohort', None)
        study_year = attrs.get('study_year', getattr(self.instance, 'study_year', 1))
        if cohort is not None and study_year > cohort.major.duration_years:
            raise serializers.ValidationError({
                'study_year': ['Study year exceeds this cohort programme duration.']
            })
        return attrs

    def create(self, validated_data):
        semester = validated_data['semester']
        validated_data['programme_semester'] = (
            (validated_data['study_year'] - 1) * 2 + semester.semester_no
        )
        return super().create(validated_data)

    def update(self, instance, validated_data):
        semester = validated_data.get('semester', instance.semester)
        study_year = validated_data.get('study_year', instance.study_year)
        validated_data['programme_semester'] = (study_year - 1) * 2 + semester.semester_no
        return super().update(instance, validated_data)


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
    teaching_period_status = serializers.CharField(source='semester.status', read_only=True)
    programme_semester = serializers.SerializerMethodField()

    class Meta:
        model = StudentPlacement
        fields = (
            'id', 'student_id', 'student', 'semester_id', 'academic_id', 'academic_year',
            'semester_no', 'semester_name', 'major_id', 'major', 'admitted_on',
            'study_year', 'programme_semester', 'teaching_period_status',
            'is_current', 'created_at',
        )
        read_only_fields = (
            'id', 'student', 'academic_id', 'academic_year', 'semester_no',
            'semester_name', 'major', 'created_at',
            'programme_semester', 'teaching_period_status',
        )

    def get_programme_semester(self, placement):
        return ((placement.study_year - 1) * 2) + placement.semester.semester_no

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
    teaching_period_name = serializers.CharField(
        source='semester.semester_name', read_only=True
    )
    period_number = serializers.IntegerField(source='semester.semester_no', read_only=True)
    teaching_period_status = serializers.CharField(source='semester.status', read_only=True)
    lecture_plan = serializers.SerializerMethodField()
    eligible_major_ids = serializers.PrimaryKeyRelatedField(
        source='eligible_majors', queryset=Major.objects.filter(is_active=True),
        many=True, required=False,
    )
    eligible_majors = MajorSerializer(many=True, read_only=True)
    definition_id = serializers.PrimaryKeyRelatedField(
        source='definition', queryset=ModuleDefinition.objects.filter(is_active=True),
        required=False,
    )

    class Meta:
        model = Module
        fields = (
            'id', 'semester_id', 'academic_id', 'academic_year',
            'teaching_period_name', 'period_number', 'teaching_period_status',
            'definition_id', 'module_code', 'module_name', 'eligible_major_ids',
            'eligible_majors', 'study_year', 'lecture_plan',
        )
        read_only_fields = (
            'id', 'academic_id', 'academic_year', 'teaching_period_name',
            'period_number', 'teaching_period_status', 'eligible_majors',
        )

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
        if (
            self.instance is not None
            and self.instance.semester.status != Semester.Status.ACTIVE
            and not self.instance.semester.academic.is_current
        ):
            raise ConflictError(
                'Historical module offerings are read-only. Copy the module into the current year.'
            )
        if (
            semester is not None
            and semester.status != Semester.Status.ACTIVE
            and not semester.academic.is_current
        ):
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

    def _resolve_definition(self, validated_data):
        definition = validated_data.get('definition')
        code = validated_data.get('module_code')
        name = validated_data.get('module_name')
        if definition is not None:
            validated_data['module_code'] = definition.code
            validated_data['module_name'] = definition.name
            return
        if code is None and self.instance is not None:
            return
        if not code or not name:
            raise serializers.ValidationError({
                'definition_id': ['Choose a catalogue module or provide its code and name.']
            })
        definition, created = ModuleDefinition.objects.get_or_create(
            code=code.strip().upper(), defaults={'name': name.strip()}
        )
        if not created and definition.name != name.strip():
            definition.name = name.strip()
            definition.is_active = True
            definition.save(update_fields=['name', 'is_active', 'updated_at'])
        validated_data['definition'] = definition
        validated_data['module_code'] = definition.code
        validated_data['module_name'] = definition.name

    def create(self, validated_data):
        self._resolve_definition(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._resolve_definition(validated_data)
        return super().update(instance, validated_data)


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


class PlacementBulkCreateSerializer(serializers.Serializer):
    student_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False
    )
    all_unplaced = serializers.BooleanField(default=False)
    teaching_period_id = serializers.PrimaryKeyRelatedField(
        source='teaching_period', queryset=Semester.objects.select_related('academic')
    )
    major_id = serializers.PrimaryKeyRelatedField(
        source='major', queryset=Major.objects.filter(is_active=True)
    )
    programme_semester = serializers.IntegerField(min_value=1, max_value=10)
    admitted_on = serializers.DateField()

    def validate(self, attrs):
        ids = attrs.get('student_ids')
        all_unplaced = attrs.get('all_unplaced', False)
        if bool(ids) == all_unplaced:
            raise serializers.ValidationError({
                'student_ids': [
                    'Provide selected students or choose all unplaced students, not both.'
                ]
            })
        major = attrs['major']
        programme_semester = attrs['programme_semester']
        if programme_semester > major.duration_years * 2:
            raise serializers.ValidationError({
                'programme_semester': [
                    f'{major.code} has only {major.duration_years * 2} semesters.'
                ]
            })
        period = attrs['teaching_period']
        expected_period = 1 if programme_semester % 2 else 2
        if period.semester_no != expected_period:
            raise serializers.ValidationError({
                'teaching_period_id': [
                    f'Semester {programme_semester} requires teaching period {expected_period}.'
                ]
            })
        return attrs


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
    start_year = serializers.IntegerField(min_value=2000, max_value=2200, required=False)
    academic_year = serializers.CharField(max_length=32, required=False)

    def validate(self, attrs):
        if 'start_year' not in attrs:
            legacy = attrs.get('academic_year', '')
            try:
                attrs['start_year'] = int(legacy[:4])
            except (TypeError, ValueError):
                raise serializers.ValidationError({'start_year': ['Enter the first year, for example 2026.']})
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
        ordered_terms = sorted(semesters, key=lambda row: row['semester_no'])
        if ordered_terms[0]['end_date'] >= ordered_terms[1]['start_date']:
            raise serializers.ValidationError({
                'semesters': ['Term 1 and Term 2 dates cannot overlap.']
            })
        start_year = attrs['academic']['start_year']
        if ordered_terms[0]['start_date'].year != start_year:
            raise serializers.ValidationError({
                'semesters': [f'Term 1 must start during {start_year}.']
            })
        if max(row['end_date'] for row in semesters) > date(start_year + 1, 12, 31):
            raise serializers.ValidationError({
                'semesters': [f'Term dates must finish by the end of {start_year + 1}.']
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
