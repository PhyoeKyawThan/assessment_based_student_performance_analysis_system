from django.test import TestCase

from catalog.models import Academic, Enrollment, Module, Semester
from core.testing import api_client, make_user
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
                'credit_hours': 3,
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

    def test_delete_is_blocked_while_semesters_exist(self):
        academic = self.make_academic()
        self.make_semester(academic)

        response = self.client.delete(f'/api/academics/{academic.pk}/')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(Academic.objects.filter(pk=academic.pk).exists())

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
            'credit_hours': 3,
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

    def test_credit_hours_bounds(self):
        for hours in (0, 31):
            response = self.create(credit_hours=hours)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('credit_hours', response.data['fields'])

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
