from datetime import date, timedelta

from django.test import TestCase

from catalog.models import Enrollment
from core.testing import api_client, make_user
from marking.models import AssessmentResult
from teaching.models import Assessment, LecturePlan
from teaching.tests import TeachingFixtureMixin
from users.models import User


class DashboardTests(TeachingFixtureMixin, TestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.other_teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)

        self.plan = self.make_publishable_plan(
            lecturer=self.teacher, status=LecturePlan.Status.PUBLISHED
        )
        self.other_plan = self.make_publishable_plan(
            lecturer=self.other_teacher, status=LecturePlan.Status.DRAFT
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, module=self.plan.module
        )

        quiz = self.plan.distributions.get(assessment_type='QUIZ')
        scheduled, graded = quiz.assessments.order_by('sequence_no')
        scheduled.assessment_date = date.today() + timedelta(days=1)
        scheduled.status = Assessment.Status.OPEN
        scheduled.save(update_fields=['assessment_date', 'status'])
        graded.assessment_date = date.today() - timedelta(days=1)
        graded.status = Assessment.Status.GRADED
        graded.save(update_fields=['assessment_date', 'status'])
        self.result = AssessmentResult.objects.create(
            assessment=graded,
            enrollment=self.enrollment,
            marks_obtained='8.00',
            graded_by=self.teacher,
        )
        self.scheduled = scheduled

    def stats_keys(self, payload):
        return [item['key'] for item in payload['stats']]

    def test_anonymous_is_rejected(self):
        response = api_client().get('/api/dashboard/')
        self.assertEqual(response.status_code, 401)

    def test_admin_sees_catalog_totals(self):
        response = self.client.get('/api/dashboard/')
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.data

        self.assertEqual(payload['role'], 'ADMIN')
        self.assertEqual(
            self.stats_keys(payload),
            ['students', 'teachers', 'modules', 'academic_year'],
        )
        by_key = {item['key']: item['value'] for item in payload['stats']}
        self.assertEqual(by_key['students'], '1')
        self.assertEqual(by_key['teachers'], '2')
        self.assertEqual(by_key['modules'], '2')
        self.assertEqual(by_key['academic_year'], '2026-2027')

        self.assertEqual(payload['donut']['total'], 1)
        enrolled = next(s for s in payload['donut']['slices'] if s['key'] == 'ENROLLED')
        self.assertEqual(enrolled['value'], 1)

        self.assertEqual([group['label'] for group in payload['bars']['groups']], ['Semester 1'])
        self.assertEqual(payload['bars']['groups'][0]['values'], {'students': 1, 'modules': 2})
        self.assertEqual(
            [row['module_code'] for row in payload['recent_modules']],
            [self.plan.module.module_code, self.other_plan.module.module_code],
        )
        self.assertEqual(len(payload['recent_enrollments']), 1)

    def test_teacher_totals_only_cover_own_plans(self):
        response = api_client(self.teacher).get('/api/dashboard/')
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.data

        self.assertEqual(payload['role'], 'TEACHER')
        by_key = {item['key']: item['value'] for item in payload['stats']}
        self.assertEqual(by_key, {
            'modules': '1',
            'draft_plans': '0',
            'published_plans': '1',
            'open_assessments': '1',
        })
        self.assertEqual(payload['donut']['total'], 1)
        self.assertEqual(
            [row['id'] for row in payload['upcoming_assessments']], [str(self.scheduled.pk)]
        )
        self.assertEqual(len(payload['recent_marks']), 1)
        self.assertEqual(payload['recent_marks'][0]['student'], self.student.full_name)
        self.assertEqual(payload['recent_marks'][0]['percentage'], '80.0')

    def test_student_sees_only_own_marks_and_published_plans(self):
        payload = api_client(self.student).get('/api/dashboard/').data

        self.assertEqual(payload['role'], 'STUDENT')
        by_key = {item['key']: item['value'] for item in payload['stats']}
        self.assertEqual(
            by_key,
            {'modules': '1', 'upcoming': '1', 'marks': '1', 'percentage': '80.0%'},
        )
        self.assertEqual([group['label'] for group in payload['bars']['groups']],
                         [self.plan.module.module_code])
        self.assertEqual(payload['bars']['groups'][0]['values']['marks'], '8.00')
        self.assertEqual(
            [row['id'] for row in payload['upcoming_assessments']], [str(self.scheduled.pk)]
        )
        self.assertEqual(len(payload['recent_marks']), 1)

    def test_draft_plan_hides_assessments_from_the_student(self):
        self.plan.status = LecturePlan.Status.DRAFT
        self.plan.save(update_fields=['status'])

        payload = api_client(self.student).get('/api/dashboard/').data

        self.assertEqual(payload['upcoming_assessments'], [])
        self.assertEqual(payload['stats'][1]['value'], '0')

    def test_superuser_gets_the_admin_dashboard(self):
        root = make_user(role=User.Role.STUDENT, is_staff=True, is_superuser=True)

        payload = api_client(root).get('/api/dashboard/').data

        self.assertEqual(payload['role'], 'ADMIN')

    def test_every_tone_is_one_the_client_palette_has(self):
        # An unknown tone renders as a blank tile or crashes the chart, so the contract is closed.
        palette_tones = {'primary', 'accent', 'success', 'warning', 'danger', 'neutral'}

        for user in (self.admin, self.teacher, self.student):
            payload = api_client(user).get('/api/dashboard/').data
            emitted = [stat['tone'] for stat in payload['stats']]
            emitted += [series['tone'] for series in payload['bars']['series']]
            self.assertEqual(emitted, [tone for tone in emitted if tone in palette_tones], payload['role'])
