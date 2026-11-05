from django.test import TestCase

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
from core.testing import api_client, make_user
from marking.models import AssessmentResult
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from users.models import User

ACADEMIC_PAYLOAD = {
    'academic_year': '2026-2027',
    'start_date': '2026-12-01',
    'end_date': '2027-09-30',
    'is_current': True,
}

SEMESTER_PAYLOAD = {
    'semester_no': 1,
    'semester_name': 'First Semester',
    'start_date': '2026-12-01',
    'end_date': '2027-03-31',
}


class CatalogFixtureMixin:
    def make_academic(self, **overrides):
        return Academic.objects.create(**{**ACADEMIC_PAYLOAD, **overrides})

    def make_semester(self, academic=None, **overrides):
        return Semester.objects.create(
            academic=academic or self.make_academic(), **{**SEMESTER_PAYLOAD, **overrides}
        )

    def make_module(self, semester=None, **overrides):
        return Module.objects.create(
            semester=semester or self.make_semester(),
            **{
                'module_code': 'CS101',
                'module_name': 'Programming Fundamentals',
                **overrides,
            },
        )


class AdminOnlyClientMixin:
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)


class AcademicTests(AdminOnlyClientMixin, CatalogFixtureMixin, TestCase):
    def test_admin_create_returns_id(self):
        response = self.client.post('/api/academics/', ACADEMIC_PAYLOAD, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['academic_year'], '2026-2027')
        self.assertIn('id', response.data)

    def test_every_role_can_read(self):
        self.make_academic()

        for user in (self.admin, self.teacher, self.student):
            response = api_client(user).get('/api/academics/')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['count'], 1)

    def test_teacher_and_student_cannot_write(self):
        for user in (self.teacher, self.student):
            response = api_client(user).post('/api/academics/', ACADEMIC_PAYLOAD, format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertEqual(response.data['code'], 'permission_denied')

    def test_duplicate_year_is_conflict(self):
        self.make_academic()

        response = self.client.post('/api/academics/', ACADEMIC_PAYLOAD, format='json')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'conflict')

    def test_end_date_must_follow_start_date(self):
        response = self.client.post(
            '/api/academics/',
            {**ACADEMIC_PAYLOAD, 'start_date': '2027-09-30', 'end_date': '2026-12-01'},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('end_date', response.data['fields'])

    def test_only_one_current_year_and_switching_clears_the_previous(self):
        first = self.client.post('/api/academics/', ACADEMIC_PAYLOAD, format='json')
        self.assertEqual(first.status_code, 201, first.data)

        second = self.client.post(
            '/api/academics/',
            {
                'academic_year': '2027-2028',
                'start_date': '2027-12-01',
                'end_date': '2028-09-30',
                'is_current': True,
            },
            format='json',
        )
        self.assertEqual(second.status_code, 201, second.data)

        first_academic = Academic.objects.get(pk=first.data['id'])
        self.assertFalse(first_academic.is_current)
        self.assertEqual(Academic.objects.filter(is_current=True).count(), 1)
        self.assertEqual(
            [row['academic_year'] for row in self.client.get('/api/academics/?is_current=true').data['results']],
            ['2027-2028'],
        )

    def test_delete_cascades_through_the_complete_academic_graph(self):
        academic = self.make_academic()
        semester = self.make_semester(academic)
        major = Major.objects.create(code='CS', name='Computer Science')
        StudentPlacement.objects.create(
            student=self.student,
            semester=semester,
            major=major,
            study_year=1,
            admitted_on=academic.start_date,
        )
        module = self.make_module(semester)
        enrollment = Enrollment.objects.create(student=self.student, module=module)
        plan = LecturePlan.objects.create(
            module=module,
            lecturer=self.teacher,
            academic=academic,
        )
        distribution = AssessmentDistribution.objects.create(
            lecture_plan=plan,
            assessment_type=AssessmentDistribution.AssessmentType.QUIZ,
            total_assessment=1,
            display_order=1,
        )
        assessment = Assessment.objects.create(
            distribution=distribution,
            title='Quiz 1',
            sequence_no=1,
            max_mark='10.00',
        )
        AssessmentResult.objects.create(
            assessment=assessment,
            enrollment=enrollment,
            marks_obtained='8.00',
            graded_by=self.teacher,
        )

        response = self.client.delete(f'/api/academics/{academic.pk}/')

        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Academic.objects.filter(pk=academic.pk).exists())
        self.assertEqual(Semester.objects.count(), 0)
        self.assertEqual(StudentPlacement.objects.count(), 0)
        self.assertEqual(Module.objects.count(), 0)
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(LecturePlan.objects.count(), 0)
        self.assertEqual(AssessmentDistribution.objects.count(), 0)
        self.assertEqual(Assessment.objects.count(), 0)
        self.assertEqual(AssessmentResult.objects.count(), 0)
        self.assertTrue(Major.objects.filter(pk=major.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())

    def test_delete_succeeds_when_nothing_references_it(self):
        academic = self.make_academic()

        response = self.client.delete(f'/api/academics/{academic.pk}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Academic.objects.filter(pk=academic.pk).exists())


class SemesterTests(AdminOnlyClientMixin, CatalogFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.academic = self.make_academic()

    def test_create_response_carries_academic_year(self):
        response = self.client.post(
            '/api/semesters/',
            {**SEMESTER_PAYLOAD, 'academic_id': str(self.academic.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['academic_year'], '2026-2027')

    def test_duplicate_semester_no_is_conflict(self):
        self.make_semester(self.academic)

        response = self.client.post(
            '/api/semesters/',
            {**SEMESTER_PAYLOAD, 'academic_id': str(self.academic.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 409, response.data)

    def test_dates_must_sit_inside_the_academic_year(self):
        response = self.client.post(
            '/api/semesters/',
            {
                **SEMESTER_PAYLOAD,
                'academic_id': str(self.academic.pk),
                'start_date': '2026-11-01',
                'end_date': '2027-03-31',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('start_date', response.data['fields'])

    def test_filter_by_academic(self):
        other = self.make_academic(academic_year='2027-2028', start_date='2027-12-01', end_date='2028-09-30')
        self.make_semester(other, semester_no=1)
        self.make_semester(self.academic, semester_no=2)

        results = self.client.get(f'/api/semesters/?academic_id={other.pk}').data['results']

        self.assertEqual([row['semester_no'] for row in results], [1])
        self.assertEqual(results[0]['academic_year'], '2027-2028')

    def test_delete_is_blocked_while_modules_exist(self):
        semester = self.make_semester(self.academic)
        self.make_module(semester)

        response = self.client.delete(f'/api/semesters/{semester.pk}/')

        self.assertEqual(response.status_code, 409, response.data)


class ModuleTests(AdminOnlyClientMixin, CatalogFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.semester = self.make_semester()

    def create(self, **overrides):
        payload = {
            'semester_id': str(self.semester.pk),
            'module_code': 'CS101',
            'module_name': 'Programming Fundamentals',
            **overrides,
        }
        return self.client.post('/api/modules/', payload, format='json')

    def test_create_response_carries_academic_fields(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['academic_year'], '2026-2027')
        self.assertEqual(response.data['academic_id'], str(self.semester.academic_id))

    def test_duplicate_code_in_one_semester_is_conflict(self):
        self.make_module(self.semester)

        response = self.create()

        self.assertEqual(response.status_code, 409, response.data)

    def test_student_only_sees_enrolled_modules(self):
        enrolled = self.make_module(self.semester, module_code='CS101')
        self.make_module(self.semester, module_code='CS202')
        Enrollment.objects.create(student=self.student, module=enrolled)

        results = api_client(self.student).get('/api/modules/').data['results']

        self.assertEqual([row['module_code'] for row in results], ['CS101'])

    def test_admin_sees_every_module(self):
        self.make_module(self.semester, module_code='CS101')
        self.make_module(self.semester, module_code='CS202')

        response = self.client.get('/api/modules/')

        self.assertEqual(response.data['count'], 2)

    def test_filters(self):
        other_academic = self.make_academic(
            academic_year='2027-2028', start_date='2027-12-01', end_date='2028-09-30'
        )
        other_semester = self.make_semester(other_academic, semester_no=1)
        self.make_module(self.semester, module_code='CS101', module_name='Programming')
        self.make_module(other_semester, module_code='EE300', module_name='Circuits')

        self.assertEqual(
            [row['module_code'] for row in self.client.get(f'/api/modules/?semester_id={self.semester.pk}').data['results']],
            ['CS101'],
        )
        self.assertEqual(
            [row['module_code'] for row in self.client.get(f'/api/modules/?academic_id={other_academic.pk}').data['results']],
            ['EE300'],
        )
        self.assertEqual(
            [row['module_code'] for row in self.client.get('/api/modules/?search=circ').data['results']],
            ['EE300'],
        )

    def test_lecture_plan_names_the_responsible_teacher(self):
        module = self.make_module(self.semester)
        teacher = make_user(role=User.Role.TEACHER)
        LecturePlan.objects.create(
            module=module, lecturer=teacher, academic=self.semester.academic
        )

        row = self.client.get(f'/api/modules/{module.pk}/').data

        self.assertEqual(set(row['lecture_plan']), {'id', 'status', 'lecturer'})
        self.assertEqual(row['lecture_plan']['lecturer']['id'], str(teacher.pk))

    def test_an_unassigned_module_reports_no_lecture_plan(self):
        self.make_module(self.semester)

        row = self.client.get('/api/modules/').data['results'][0]

        self.assertIsNone(row['lecture_plan'])

    def test_assignment_filters(self):
        teacher = make_user(role=User.Role.TEACHER)
        assigned = self.make_module(self.semester, module_code='CS101')
        LecturePlan.objects.create(
            module=assigned, lecturer=teacher, academic=self.semester.academic
        )
        self.make_module(self.semester, module_code='CS202')

        for query, expected in (
            ('?planned=true', ['CS101']),
            ('?planned=false', ['CS202']),
            (f'?lecturer_id={teacher.pk}', ['CS101']),
        ):
            rows = self.client.get(f'/api/modules/{query}').data['results']
            self.assertEqual([row['module_code'] for row in rows], expected, query)

    def test_current_year_filter(self):
        past = self.make_academic(
            academic_year='2025-2026', is_current=False,
            start_date='2025-12-01', end_date='2026-09-30',
        )
        self.make_module(self.make_semester(past), module_code='CS001')
        self.make_module(self.semester, module_code='CS101')

        rows = self.client.get('/api/modules/?current_year=true').data['results']

        self.assertEqual([row['module_code'] for row in rows], ['CS101'])

    def test_malformed_filter_is_400(self):
        response = self.client.get('/api/modules/?academic_id=not-a-uuid')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('academic_id', response.data['fields'])

    def test_delete_is_blocked_while_enrollments_exist(self):
        module = self.make_module(self.semester)
        Enrollment.objects.create(student=self.student, module=module)

        response = self.client.delete(f'/api/modules/{module.pk}/')

        self.assertEqual(response.status_code, 409, response.data)


class EnrollmentTests(AdminOnlyClientMixin, CatalogFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.module = self.make_module()
        self.second_student = make_user(role=User.Role.STUDENT)
        self.major = Major.objects.create(code='CS', name='Computer Science')
        for student in (self.student, self.second_student):
            StudentPlacement.objects.create(
                student=student,
                semester=self.module.semester,
                major=self.major,
                admitted_on='2026-12-01',
                is_current=True,
            )

    def create(self, student=None, module=None):
        return self.client.post(
            '/api/enrollments/',
            {
                'student_id': str((student or self.student).pk),
                'module_id': str((module or self.module).pk),
            },
            format='json',
        )

    def test_admin_create_sets_status_and_nested_shapes(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'ENROLLED')
        self.assertIsNotNone(response.data['enrolled_at'])
        self.assertEqual(
            set(response.data['student']), {'id', 'full_name', 'email'}
        )
        self.assertEqual(
            set(response.data['module']), {'id', 'module_code', 'module_name'}
        )

    def test_teacher_cannot_create(self):
        response = api_client(self.teacher).post(
            '/api/enrollments/',
            {'student_id': str(self.student.pk), 'module_id': str(self.module.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_non_student_is_rejected(self):
        response = self.create(student=self.teacher)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('student_id', response.data['fields'])

    def test_duplicate_enrollment_is_conflict(self):
        self.create()

        response = self.create()

        self.assertEqual(response.status_code, 409, response.data)

    def test_list_visibility_by_role(self):
        self.create()

        self.assertEqual(api_client(self.student).get('/api/enrollments/').data['count'], 1)
        self.assertEqual(self.client.get('/api/enrollments/').data['count'], 1)
        self.assertEqual(api_client(self.teacher).get('/api/enrollments/').data['count'], 0)

    def test_patch_changes_status_only(self):
        enrollment = self.create().data

        response = self.client.patch(
            f"/api/enrollments/{enrollment['id']}/", {'status': 'DROPPED'}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'DROPPED')
        self.assertEqual(response.data['id'], enrollment['id'])

    def test_delete_succeeds_while_no_results_exist(self):
        enrollment = self.create().data

        response = self.client.delete(f"/api/enrollments/{enrollment['id']}/")

        self.assertEqual(response.status_code, 204)

    def test_bulk_creates_and_skips(self):
        Enrollment.objects.create(student=self.student, module=self.module)

        response = self.client.post(
            '/api/enrollments/bulk/',
            {
                'module_id': str(self.module.pk),
                'student_ids': [
                    str(self.student.pk),
                    str(self.second_student.pk),
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['created']), 1)
        self.assertEqual(
            response.data['skipped'],
            [{'student_id': str(self.student.pk), 'reason': 'already_enrolled'}],
        )
        self.assertEqual(Enrollment.objects.filter(module=self.module).count(), 2)

    def test_bulk_rejects_teacher_id_and_creates_nothing(self):
        response = self.client.post(
            '/api/enrollments/bulk/',
            {
                'module_id': str(self.module.pk),
                'student_ids': [str(self.second_student.pk), str(self.teacher.pk)],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('student_ids', response.data['fields'])
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_bulk_rejects_unknown_student_and_creates_nothing(self):
        response = self.client.post(
            '/api/enrollments/bulk/',
            {
                'module_id': str(self.module.pk),
                'student_ids': ['00000000-0000-4000-8000-000000000000'],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_bulk_is_admin_only(self):
        response = api_client(self.teacher).post(
            '/api/enrollments/bulk/',
            {'module_id': str(self.module.pk), 'student_ids': [str(self.student.pk)]},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    def test_me_enrollments_is_student_only(self):
        self.create()

        response = api_client(self.student).get('/api/me/enrollments/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['student']['id'], str(self.student.pk))
        self.assertEqual(api_client(self.admin).get('/api/me/enrollments/').status_code, 403)

    def test_user_delete_is_blocked_while_enrolled(self):
        self.create()

        response = self.client.delete(f'/api/users/{self.student.pk}/')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())


class AcademicWorkflowTests(AdminOnlyClientMixin, CatalogFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.current = self.make_academic()
        self.current_semester = self.make_semester(self.current)
        self.past = self.make_academic(
            academic_year='2025-2026',
            start_date='2025-12-01',
            end_date='2026-09-30',
            is_current=False,
        )
        self.past_semester = self.make_semester(self.past)
        self.cs = Major.objects.create(code='CS', name='Computer Science')
        self.is_major = Major.objects.create(code='IS', name='Information Systems')

    def test_copy_historical_module_copies_placement_and_majors_only(self):
        source = self.make_module(self.past_semester)
        source.eligible_majors.add(self.cs)
        LecturePlan.objects.create(
            module=source, lecturer=self.teacher, academic=self.past
        )

        response = self.client.post(
            f'/api/modules/{source.pk}/copy/',
            {'target_semester_id': str(self.current_semester.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        copied = Module.objects.get(pk=response.data['id'])
        self.assertEqual(copied.semester, self.current_semester)
        self.assertEqual(copied.module_code, source.module_code)
        self.assertEqual(list(copied.eligible_majors.all()), [self.cs])
        self.assertFalse(hasattr(copied, 'lecture_plan'))

    def test_copy_rejects_a_non_current_target(self):
        source = self.make_module(self.current_semester)

        response = self.client.post(
            f'/api/modules/{source.pk}/copy/',
            {'target_semester_id': str(self.past_semester.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('target_semester_id', response.data['fields'])

    def test_historical_module_is_read_only(self):
        historical = self.make_module(self.past_semester)

        patch_response = self.client.patch(
            f'/api/modules/{historical.pk}/', {'module_name': 'Changed'}, format='json'
        )
        delete_response = self.client.delete(f'/api/modules/{historical.pk}/')

        self.assertEqual(patch_response.status_code, 409, patch_response.data)
        self.assertEqual(delete_response.status_code, 409, delete_response.data)
        historical.refresh_from_db()
        self.assertEqual(historical.module_name, 'Programming Fundamentals')

    def test_eligible_students_match_semester_and_major(self):
        module = self.make_module(self.current_semester)
        module.eligible_majors.add(self.cs)
        eligible = self.student
        wrong_major = make_user(role=User.Role.STUDENT)
        no_placement = make_user(role=User.Role.STUDENT)
        StudentPlacement.objects.create(
            student=eligible, semester=self.current_semester, major=self.cs,
            admitted_on='2026-12-01', is_current=True,
        )
        StudentPlacement.objects.create(
            student=wrong_major, semester=self.current_semester, major=self.is_major,
            admitted_on='2026-12-01', is_current=True,
        )

        rows = self.client.get(
            f'/api/modules/{module.pk}/eligible-students/'
        ).data['results']

        self.assertEqual([row['id'] for row in rows], [str(eligible.pk)])
        for student in (wrong_major, no_placement):
            response = self.client.post(
                '/api/enrollments/',
                {'student_id': str(student.pk), 'module_id': str(module.pk)},
                format='json',
            )
            self.assertEqual(response.status_code, 400, response.data)

    def test_batch_promotion_preserves_the_previous_placement(self):
        source = StudentPlacement.objects.create(
            student=self.student,
            semester=self.past_semester,
            major=self.cs,
            admitted_on='2025-12-01',
            is_current=True,
        )

        response = self.client.post(
            '/api/student-placements/promote/',
            {
                'student_ids': [str(self.student.pk)],
                'target_semester_id': str(self.current_semester.pk),
                'target_study_year': 2,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        source.refresh_from_db()
        self.assertFalse(source.is_current)
        promoted = StudentPlacement.objects.get(
            student=self.student, semester=self.current_semester
        )
        self.assertTrue(promoted.is_current)
        self.assertEqual(promoted.major, self.cs)
        self.assertEqual(promoted.study_year, 2)
        self.assertEqual(str(promoted.admitted_on), '2025-12-01')

    def test_bulk_initial_placement_creates_many_students_with_one_request(self):
        second = make_user(role=User.Role.STUDENT)

        response = self.client.post(
            '/api/student-placements/bulk/',
            {
                'student_ids': [str(self.student.pk), str(second.pk)],
                'teaching_period_id': str(self.current_semester.pk),
                'major_id': str(self.cs.pk),
                'programme_semester': 1,
                'admitted_on': '2026-12-01',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['created_count'], 2)
        self.assertEqual(
            StudentPlacement.objects.filter(is_current=True, major=self.cs).count(), 2
        )

    def test_bulk_all_unplaced_does_not_move_students_with_a_current_placement(self):
        unplaced = make_user(role=User.Role.STUDENT)
        StudentPlacement.objects.create(
            student=self.student, semester=self.past_semester, major=self.is_major,
            admitted_on='2025-12-01', is_current=True,
        )

        response = self.client.post(
            '/api/student-placements/bulk/',
            {
                'all_unplaced': True,
                'teaching_period_id': str(self.current_semester.pk),
                'major_id': str(self.cs.pk),
                'programme_semester': 1,
                'admitted_on': '2026-12-01',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['created_count'], 1)
        self.assertTrue(StudentPlacement.objects.filter(student=unplaced).exists())
        self.assertEqual(
            StudentPlacement.objects.get(student=self.student).major, self.is_major
        )

    def test_user_list_can_show_only_unplaced_students(self):
        unplaced = make_user(role=User.Role.STUDENT)
        StudentPlacement.objects.create(
            student=self.student, semester=self.current_semester, major=self.cs,
            admitted_on='2026-12-01', is_current=True,
        )

        response = self.client.get('/api/users/?role=STUDENT&placement_status=unplaced')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['id'] for row in response.data['results']], [str(unplaced.pk)])


class AcademicSetupDraftTests(AdminOnlyClientMixin, TestCase):
    def payload(self):
        return {
            'academic': {
                'academic_year': '2027-2028',
                'start_date': '2027-11-01',
                'end_date': '2028-09-30',
            },
            'semesters': [
                {
                    'semester_no': 1,
                    'semester_name': 'Semester 1',
                    'start_date': '2027-11-01',
                    'end_date': '2028-03-31',
                },
                {
                    'semester_no': 2,
                    'semester_name': 'Semester 2',
                    'start_date': '2028-04-01',
                    'end_date': '2028-09-30',
                },
            ],
            'majors': [
                {'code': 'CS', 'name': 'Computer Science', 'duration_years': 4},
                {'code': 'ARCH', 'name': 'Architecture', 'duration_years': 5},
            ],
            'modules': [
                {
                    'module_code': 'CS101',
                    'module_name': 'Programming',
                    'study_year': 1,
                    'semester_no': 1,
                    'major_codes': ['CS'],
                },
                {
                    'module_code': 'AR501',
                    'module_name': 'Architecture Studio',
                    'study_year': 5,
                    'semester_no': 2,
                    'major_codes': ['ARCH'],
                },
            ],
        }

    def create_draft(self, data=None):
        response = self.client.post(
            '/api/academic-setup-drafts/',
            {'name': 'Next year', 'current_step': 1, 'data': data or self.payload()},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_draft_can_be_saved_without_creating_live_records(self):
        response = self.create_draft({'academic': {'academic_year': '2027-2028'}})

        self.assertEqual(Academic.objects.count(), 0)
        updated = self.client.patch(
            f'/api/academic-setup-drafts/{response.data["id"]}/',
            {'current_step': 2, 'data': self.payload()},
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['current_step'], 2)
        self.assertEqual(Academic.objects.count(), 0)

    def test_publish_creates_the_complete_setup_atomically(self):
        old = Academic.objects.create(
            academic_year='2026-2027', start_date='2026-11-01',
            end_date='2027-09-30', is_current=True,
        )
        draft = self.create_draft()

        response = self.client.post(
            f'/api/academic-setup-drafts/{draft.data["id"]}/publish/', {}, format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        academic = Academic.objects.get(academic_year='2027-2028')
        old.refresh_from_db()
        self.assertTrue(academic.is_current)
        self.assertFalse(old.is_current)
        self.assertEqual(academic.semesters.count(), 2)
        self.assertEqual(Major.objects.get(code='ARCH').duration_years, 5)
        self.assertEqual(Module.objects.get(module_code='AR501').study_year, 5)
        self.assertEqual(ModuleDefinition.objects.count(), 2)
        self.assertEqual(CurriculumVersion.objects.filter(status='ACTIVE').count(), 2)
        self.assertEqual(CurriculumModule.objects.count(), 2)
        self.assertEqual(
            AcademicSetupDraft.objects.get(pk=draft.data['id']).status,
            AcademicSetupDraft.Status.PUBLISHED,
        )

    def test_invalid_publish_creates_nothing(self):
        data = self.payload()
        data['modules'][1]['major_codes'] = ['CS']
        draft = self.create_draft(data)

        response = self.client.post(
            f'/api/academic-setup-drafts/{draft.data["id"]}/publish/', {}, format='json'
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Academic.objects.count(), 0)
        self.assertEqual(Semester.objects.count(), 0)
        self.assertEqual(Module.objects.count(), 0)

    def test_start_year_and_term_dates_derive_the_session_range(self):
        data = self.payload()
        data['academic'] = {'start_year': 2027}
        draft = self.create_draft(data)

        response = self.client.post(
            f'/api/academic-setup-drafts/{draft.data["id"]}/publish/', {}, format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        academic = Academic.objects.get(academic_year='2027-2028')
        self.assertEqual(str(academic.start_date), '2027-11-01')
        self.assertEqual(str(academic.end_date), '2028-09-30')


class CurriculumArchitectureTests(AdminOnlyClientMixin, CatalogFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.academic = self.make_academic()
        self.term_one = self.make_semester(self.academic)
        self.term_two = self.make_semester(
            self.academic,
            semester_no=2,
            semester_name='Second Semester',
            start_date='2027-04-01',
            end_date='2027-09-30',
        )
        self.major = Major.objects.create(code='CS', name='Computer Science')
        self.definition = ModuleDefinition.objects.create(code='CS101', name='Programming')
        self.curriculum = CurriculumVersion.objects.create(
            major=self.major, name='CS Curriculum 2026',
            effective_from=self.academic, status=CurriculumVersion.Status.ACTIVE,
        )
        CurriculumModule.objects.create(
            curriculum=self.curriculum, module_definition=self.definition,
            study_year=1, term_number=1,
        )

    def test_generate_offerings_is_repeatable_and_keeps_curriculum_separate(self):
        url = f'/api/curricula/{self.curriculum.pk}/generate-offerings/'
        first = self.client.post(url, {'academic_id': str(self.academic.pk)}, format='json')
        second = self.client.post(url, {'academic_id': str(self.academic.pk)}, format='json')

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(len(first.data), 1)
        self.assertEqual(second.data, [])
        offering = Module.objects.get()
        self.assertEqual(offering.definition, self.definition)
        self.assertEqual(list(offering.eligible_majors.all()), [self.major])

    def test_legacy_module_creation_reuses_the_permanent_definition(self):
        first = self.make_module(self.term_one, module_code='CS202', module_name='Algorithms')
        second = self.make_module(self.term_two, module_code='CS202', module_name='Algorithms')

        self.assertEqual(first.definition_id, second.definition_id)
        self.assertEqual(ModuleDefinition.objects.filter(code='CS202').count(), 1)

    def test_cohort_term_derives_programme_semester(self):
        cohort = Cohort.objects.create(
            name='CS 2026 Intake', admission_year=2026, major=self.major,
            curriculum=self.curriculum,
        )

        response = self.client.post(
            '/api/cohort-terms/',
            {
                'cohort_id': str(cohort.pk),
                'semester_id': str(self.term_two.pk),
                'study_year': 3,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['programme_semester'], 6)
        self.assertEqual(CohortTerm.objects.get().programme_semester, 6)

    def test_curriculum_modules_use_fixed_programme_semester_numbers(self):
        response = self.client.get(f'/api/curricula/{self.curriculum.pk}/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['module_slots'][0]['semester_number'], 1)

        created = self.client.post(
            '/api/curriculum-modules/',
            {
                'curriculum_id': str(self.curriculum.pk),
                'module_definition_id': str(
                    ModuleDefinition.objects.create(code='CS408', name='Capstone').pk
                ),
                'semester_number': 8,
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['semester_number'], 8)
        slot = CurriculumModule.objects.get(module_definition__code='CS408')
        self.assertEqual((slot.study_year, slot.term_number), (4, 2))

    def test_admin_creates_a_teaching_period_without_managing_an_academic_record(self):
        response = self.client.post(
            '/api/teaching-periods/',
            {
                'academic_label': '2027-2028',
                'period_number': 1,
                'name': 'First teaching period',
                'start_date': '2027-11-01',
                'end_date': '2028-03-31',
                'status': 'ACTIVE',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['academic_label'], '2027-2028')
        self.assertEqual(response.data['period_number'], 1)
        self.assertEqual(Semester.objects.get(pk=response.data['id']).status, 'ACTIVE')

    def test_generate_offerings_for_one_fixed_semester_and_teaching_period(self):
        url = f'/api/curricula/{self.curriculum.pk}/generate-offerings/'
        response = self.client.post(
            url,
            {
                'teaching_period_id': str(self.term_one.pk),
                'programme_semester': 1,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['module_code'], 'CS101')
