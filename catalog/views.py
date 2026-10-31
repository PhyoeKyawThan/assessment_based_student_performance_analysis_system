from django.db.models import ProtectedError, Q
from django.db.transaction import atomic
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from catalog.models import (
    Academic,
    AcademicSetupDraft,
    Enrollment,
    Major,
    Module,
    Semester,
    StudentPlacement,
)
from catalog.serializers import (
    AcademicSerializer,
    AcademicSetupDraftSerializer,
    AcademicSetupPublishSerializer,
    EnrollmentBulkSerializer,
    EnrollmentSerializer,
    EnrollmentStatusSerializer,
    MajorSerializer,
    ModuleCopySerializer,
    ModuleSerializer,
    PlacementPromotionSerializer,
    SemesterSerializer,
    StudentPlacementSerializer,
    UserBriefSerializer,
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


class MajorViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Major.objects.all()
    serializer_class = MajorSerializer
    representation_serializer_class = MajorSerializer
    permission_classes = (IsAdminRoleOrReadOnly,)

    def get_queryset(self):
        queryset = super().get_queryset()
        raw = self.request.query_params.get('is_active')
        if raw not in (None, ''):
            queryset = queryset.filter(is_active=bool_param('is_active', raw))
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(Q(code__icontains=search) | Q(name__icontains=search))
        return queryset

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ConflictError(
                'Student semester history references this major. Deactivate it instead.'
            )


class ModuleViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = Module.objects.select_related(
        'semester__academic', 'lecture_plan__lecturer'
    ).prefetch_related('eligible_majors')
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

        raw = params.get('major_id')
        if raw not in (None, ''):
            queryset = queryset.filter(eligible_majors=uuid_param('major_id', raw))

        raw = params.get('study_year')
        if raw not in (None, ''):
            try:
                queryset = queryset.filter(study_year=int(raw))
            except ValueError:
                raise ValidationError({'study_year': ['Expected a number.']})

        search = params.get('search')
        if search:
            queryset = queryset.filter(
                Q(module_code__icontains=search) | Q(module_name__icontains=search)
            )
        return queryset

    @action(detail=True, methods=['post'], permission_classes=(IsAdminRole,))
    def copy(self, request, pk=None):
        source = self.get_object()
        serializer = ModuleCopySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = serializer.validated_data['target_semester']
        if not target.academic.is_current:
            raise ValidationError({
                'target_semester_id': ['Modules may only be copied into the current academic year.']
            })
        if Module.objects.filter(
            semester=target, study_year=source.study_year, module_code=source.module_code
        ).exists():
            raise ConflictError(
                f'{source.module_code} already exists in {target.semester_name}.',
                fields={'target_semester_id': ['Choose another semester or use the existing module.']},
            )
        with atomic():
            copied = Module.objects.create(
                semester=target,
                module_code=source.module_code,
                module_name=source.module_name,
                study_year=source.study_year,
            )
            copied.eligible_majors.set(source.eligible_majors.all())
        return Response(self.representation_serializer_class(copied).data, status=201)

    @action(
        detail=True, methods=['get'], url_path='eligible-students',
        permission_classes=(IsAdminRole,),
    )
    def eligible_students(self, request, pk=None):
        module = self.get_object()
        placements = StudentPlacement.objects.filter(
            semester=module.semester, study_year=module.study_year, is_current=True
        )
        major_ids = module.eligible_majors.values_list('pk', flat=True)
        if major_ids:
            placements = placements.filter(major_id__in=major_ids)
        users = User.objects.filter(
            role=User.Role.STUDENT, placements__in=placements,
        ).exclude(enrollments__module=module).distinct().order_by('full_name', 'email')
        search = request.query_params.get('search')
        if search:
            users = users.filter(
                Q(full_name__icontains=search)
                | Q(email__icontains=search)
                | Q(username__icontains=search)
            )
        page = self.paginate_queryset(users)
        data = UserBriefSerializer(page if page is not None else users, many=True).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    def perform_destroy(self, instance):
        if not instance.semester.academic.is_current:
            raise ConflictError(
                'Historical module offerings are read-only and cannot be deleted.'
            )
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
            created = []
            for pk in student_ids:
                if pk in already:
                    continue
                row = EnrollmentSerializer(
                    data={'module_id': str(module.pk), 'student_id': str(pk)},
                    context={'request': request},
                )
                row.is_valid(raise_exception=True)
                created.append(row.save())

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


class StudentPlacementViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = StudentPlacement.objects.select_related(
        'student', 'semester__academic', 'major'
    )
    serializer_class = StudentPlacementSerializer
    representation_serializer_class = StudentPlacementSerializer
    permission_classes = (IsAdminRole,)

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        for param, lookup in (
            ('student_id', 'student'),
            ('semester_id', 'semester'),
            ('academic_id', 'semester__academic'),
            ('major_id', 'major'),
        ):
            raw = params.get(param)
            if raw not in (None, ''):
                queryset = queryset.filter(**{lookup: uuid_param(param, raw)})
        raw = params.get('study_year')
        if raw not in (None, ''):
            try:
                queryset = queryset.filter(study_year=int(raw))
            except ValueError:
                raise ValidationError({'study_year': ['Expected a number.']})
        raw = params.get('is_current')
        if raw not in (None, ''):
            queryset = queryset.filter(is_current=bool_param('is_current', raw))
        return queryset

    @action(detail=False, methods=['post'])
    def promote(self, request):
        serializer = PlacementPromotionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student_ids = list(dict.fromkeys(serializer.validated_data['student_ids']))
        target = serializer.validated_data['target_semester']
        selected_major = serializer.validated_data.get('major')
        target_study_year = serializer.validated_data['target_study_year']
        current = {
            row.student_id: row
            for row in StudentPlacement.objects.select_related('major').filter(
                student_id__in=student_ids, is_current=True
            )
        }
        missing = [str(pk) for pk in student_ids if pk not in current]
        if missing:
            raise ValidationError({
                'student_ids': [f'{pk} has no current semester placement.' for pk in missing]
            })

        promoted = []
        with atomic():
            for student_id in student_ids:
                previous = current[student_id]
                placement, _created = StudentPlacement.objects.update_or_create(
                    student_id=student_id,
                    semester=target,
                    defaults={
                        'major': selected_major or previous.major,
                        'study_year': target_study_year,
                        'admitted_on': previous.admitted_on,
                        'is_current': True,
                    },
                )
                placement.make_current()
                promoted.append(placement)
        return Response(self.representation_serializer_class(promoted, many=True).data)


class AcademicSetupDraftViewSet(RepresentationMixin, viewsets.ModelViewSet):
    queryset = AcademicSetupDraft.objects.select_related('created_by')
    serializer_class = AcademicSetupDraftSerializer
    representation_serializer_class = AcademicSetupDraftSerializer
    permission_classes = (IsAdminRole,)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.status != AcademicSetupDraft.Status.DRAFT:
            raise ConflictError('A published setup can no longer be edited.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != AcademicSetupDraft.Status.DRAFT:
            raise ConflictError('Only an unpublished setup draft can be deleted.')
        instance.delete()

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        draft = self.get_object()
        if draft.status != AcademicSetupDraft.Status.DRAFT:
            raise ConflictError('This setup draft has already been published or discarded.')

        serializer = AcademicSetupPublishSerializer(data=draft.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if Academic.objects.filter(
            academic_year=data['academic']['academic_year']
        ).exists():
            raise ConflictError(
                'That academic year already exists.',
                fields={'academic_year': ['Choose another academic year.']},
            )

        with atomic():
            academic = Academic.objects.create(**data['academic'], is_current=True)
            academic.clear_other_current()
            semesters = {
                row['semester_no']: Semester.objects.create(academic=academic, **row)
                for row in data['semesters']
            }
            majors = {}
            for row in data['majors']:
                code = row['code'].upper()
                major, _created = Major.objects.update_or_create(
                    code=code,
                    defaults={
                        'name': row['name'],
                        'duration_years': row['duration_years'],
                        'is_active': True,
                    },
                )
                majors[code] = major
            created_modules = []
            for row in data['modules']:
                module = Module.objects.create(
                    semester=semesters[row['semester_no']],
                    study_year=row['study_year'],
                    module_code=row['module_code'].upper(),
                    module_name=row['module_name'],
                )
                module.eligible_majors.set(majors[code.upper()] for code in row['major_codes'])
                created_modules.append(module)
            draft.status = AcademicSetupDraft.Status.PUBLISHED
            draft.current_step = 4
            draft.save(update_fields=['status', 'current_step', 'updated_at'])

        return Response(
            {
                'draft': self.representation_serializer_class(draft).data,
                'academic_id': str(academic.id),
                'semester_ids': [str(semester.id) for semester in semesters.values()],
                'module_ids': [str(module.id) for module in created_modules],
            },
            status=status.HTTP_201_CREATED,
        )
