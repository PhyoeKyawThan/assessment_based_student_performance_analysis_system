from django.test import TestCase

from catalog.models import Enrollment
from core.testing import api_client, make_user, mock_push_transport
from marking.models import AssessmentResult
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from teaching.tests import TeachingFixtureMixin
from users.models import User


class MarkingTestCase(TeachingFixtureMixin, TestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.other_teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(full_name='Aye Aye', role=User.Role.STUDENT)
        self.second_student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)
        self.teacher_client = api_client(self.teacher)
        self.plan = self.make_publishable_plan(
            lecturer=self.teacher, status=LecturePlan.Status.PUBLISHED
        )
        self.quiz = self.plan.distributions.get(assessment_type='QUIZ')
        self.final = self.plan.distributions.get(assessment_type='FINAL')
        self.quiz_one = self.quiz.assessments.order_by('sequence_no').first()
        self.quiz_two = self.quiz.assessments.order_by('sequence_no').last()
        self.final_exam = self.final.assessments.first()
        self.enrollment = self.enroll(self.student)
        self.second_enrollment = self.enroll(self.second_student)

    def enroll(self, user):
        return Enrollment.objects.create(student=user, module=self.plan.module)

    def open(self, assessment):
        response = self.client.post(f'/api/assessments/{assessment.pk}/open/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def payload(self, assessment, enrollment, marks='8.00', **extra):
        return {
            'assessment_id': str(assessment.pk),
            'enrollment_id': str(enrollment.pk),
            'marks_obtained': marks,
            **extra,
        }

    def bulk_payload(self, assessment, rows):
        return {
            'assessment_id': str(assessment.pk),
            'results': [
                {'enrollment_id': str(enrollment.pk), 'marks_obtained': marks}
                for enrollment, marks in rows
            ],
        }

    def grade(self, assessment):
        return self.client.post(f'/api/assessments/{assessment.pk}/mark-graded/', {}, format='json')


class SingleResultTests(MarkingTestCase):
    def test_admin_post_returns_the_contract_shape(self):
        self.open(self.quiz_one)

        response = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            set(response.data),
            {
                'id', 'assessment_id', 'enrollment_id', 'marks_obtained', 'remark',
                'graded_by', 'graded_at', 'max_mark', 'percentage',
            },
        )
        self.assertEqual(set(response.data['graded_by']), {'id', 'full_name'})
        self.assertEqual(response.data['graded_by']['id'], str(self.admin.pk))
        self.assertEqual(response.data['max_mark'], '10.00')
        self.assertEqual(response.data['percentage'], '80.0')
        self.assertEqual(AssessmentResult.objects.count(), 1)

    def test_owning_teacher_posts(self):
        self.open(self.quiz_one)

        response = self.teacher_client.post(
            '/api/assessment-results/',
            self.payload(self.quiz_one, self.enrollment, remark='Late by one day'),
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['graded_by']['id'], str(self.teacher.pk))
        self.assertEqual(response.data['remark'], 'Late by one day')

    def test_duplicate_is_conflict(self):
        self.open(self.quiz_one)
        self.client.post('/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json')

        response = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 409, response.data)

    def test_marks_above_max_mark_are_rejected(self):
        self.open(self.quiz_one)

        response = self.client.post(
            '/api/assessment-results/',
            self.payload(self.quiz_one, self.enrollment, marks='10.01'),
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('marks_obtained', response.data['fields'])

    def test_negative_marks_are_rejected(self):
        self.open(self.quiz_one)

        response = self.client.post(
            '/api/assessment-results/',
            self.payload(self.quiz_one, self.enrollment, marks='-1.00'),
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('marks_obtained', response.data['fields'])

    def test_enrollment_from_another_module_is_rejected(self):
        self.open(self.quiz_one)
        foreign = Enrollment.objects.create(
            student=self.student, module=self.make_module(module_code='CS202')
        )

        response = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, foreign), format='json'
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('enrollment_id', response.data['fields'])

    def test_dropped_enrollment_is_conflict(self):
        self.open(self.quiz_one)
        self.enrollment.status = Enrollment.Status.DROPPED
        self.enrollment.save()

        response = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 409, response.data)

    def test_scheduled_assessment_is_conflict(self):
        response = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(AssessmentResult.objects.count(), 0)

    def test_unpublished_plan_is_conflict(self):
        draft = self.make_plan(lecturer=self.teacher)
        distribution = self.make_distribution(plan=draft)
        assessment = self.make_assessment(distribution=distribution)
        enrollment = Enrollment.objects.create(student=self.student, module=draft.module)

        response = self.client.post(
            '/api/assessment-results/', self.payload(assessment, enrollment), format='json'
        )

        self.assertEqual(response.status_code, 409, response.data)

    def test_marks_are_still_editable_while_closed(self):
        self.open(self.quiz_one)
        self.client.post(f'/api/assessments/{self.quiz_one.pk}/close/', {}, format='json')

        response = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 201, response.data)

    def test_student_cannot_post(self):
        self.open(self.quiz_one)

        response = api_client(self.student).post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_other_teacher_cannot_post(self):
        self.open(self.quiz_one)

        response = api_client(self.other_teacher).post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_patch_corrects_the_mark_and_reassigns_the_grader(self):
        self.open(self.quiz_one)
        created = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        ).data

        response = self.teacher_client.patch(
            f"/api/assessment-results/{created['id']}/",
            {'marks_obtained': '9.50'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['marks_obtained'], '9.50')
        self.assertEqual(response.data['percentage'], '95.0')
        self.assertEqual(response.data['graded_by']['id'], str(self.teacher.pk))
        self.assertEqual(AssessmentResult.objects.count(), 1)

    def test_delete(self):
        self.open(self.quiz_one)
        created = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        ).data

        response = self.client.delete(f"/api/assessment-results/{created['id']}/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(AssessmentResult.objects.count(), 0)


class BulkResultTests(MarkingTestCase):
    def test_bulk_creates_then_updates(self):
        self.open(self.quiz_one)

        created = self.teacher_client.post(
            '/api/assessment-results/bulk/',
            self.bulk_payload(
                self.quiz_one, [(self.enrollment, '8.00'), (self.second_enrollment, '5.00')]
            ),
            format='json',
        )

        self.assertEqual(created.status_code, 200, created.data)
        self.assertEqual(len(created.data), 2)
        self.assertEqual(
            sorted(row['percentage'] for row in created.data), ['50.0', '80.0']
        )

        again = self.teacher_client.post(
            '/api/assessment-results/bulk/',
            self.bulk_payload(self.quiz_one, [(self.enrollment, '9.00')]),
            format='json',
        )

        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(len(again.data), 1)
        self.assertEqual(AssessmentResult.objects.filter(assessment=self.quiz_one).count(), 2)

    def test_bulk_is_atomic_when_a_row_is_invalid(self):
        self.open(self.quiz_one)
        unknown = '00000000-0000-4000-8000-000000000000'

        response = self.teacher_client.post(
            '/api/assessment-results/bulk/',
            {
                'assessment_id': str(self.quiz_one.pk),
                'results': [
                    {'enrollment_id': str(self.enrollment.pk), 'marks_obtained': '8.00'},
                    {'enrollment_id': unknown, 'marks_obtained': '7.00'},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('results', response.data['fields'])
        self.assertEqual(AssessmentResult.objects.count(), 0)

    def test_bulk_rejects_marks_over_max_mark(self):
        self.open(self.quiz_one)

        response = self.teacher_client.post(
            '/api/assessment-results/bulk/',
            self.bulk_payload(self.quiz_one, [(self.enrollment, '11.00')]),
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data['fields']['results'],
            ['Row 0: marks_obtained must be between 0 and 10.00.'],
        )

    def test_bulk_rejects_a_repeated_enrollment(self):
        self.open(self.quiz_one)

        response = self.teacher_client.post(
            '/api/assessment-results/bulk/',
            self.bulk_payload(
                self.quiz_one, [(self.enrollment, '8.00'), (self.enrollment, '7.00')]
            ),
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('results', response.data['fields'])

    def test_bulk_rejects_a_dropped_enrollment(self):
        self.open(self.quiz_one)
        self.enrollment.status = Enrollment.Status.DROPPED
        self.enrollment.save()

        response = self.teacher_client.post(
            '/api/assessment-results/bulk/',
            self.bulk_payload(self.quiz_one, [(self.enrollment, '8.00')]),
            format='json',
        )

        self.assertEqual(response.status_code, 409, response.data)

    def test_bulk_is_forbidden_for_a_stranger_and_a_student(self):
        self.open(self.quiz_one)
        payload = self.bulk_payload(self.quiz_one, [(self.enrollment, '8.00')])

        for client in (api_client(self.other_teacher), api_client(self.student)):
            response = client.post('/api/assessment-results/bulk/', payload, format='json')
            self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(AssessmentResult.objects.count(), 0)


class ResultReadTests(MarkingTestCase):
    def setUp(self):
        super().setUp()
        self.open(self.quiz_one)
        self.first_row = self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        ).data
        self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.second_enrollment),
            format='json',
        )

    def test_students_only_see_their_own_rows(self):
        rows = api_client(self.student).get('/api/assessment-results/').data

        self.assertEqual(rows['count'], 1)
        self.assertEqual(rows['results'][0]['id'], self.first_row['id'])

    def test_another_students_row_is_not_found(self):
        response = api_client(self.second_student).get(
            f"/api/assessment-results/{self.first_row['id']}/"
        )

        self.assertEqual(response.status_code, 404, response.data)

    def test_owning_teacher_sees_the_module_rows(self):
        self.assertEqual(
            api_client(self.teacher).get('/api/assessment-results/').data['count'], 2
        )
        self.assertEqual(api_client(self.other_teacher).get('/api/assessment-results/').data['count'], 0)

    def test_filters(self):
        for query, expected in (
            (f'?assessment_id={self.quiz_one.pk}', 2),
            (f'?enrollment_id={self.enrollment.pk}', 1),
            (f'?module_id={self.plan.module_id}', 2),
            (f'?student_id={self.student.pk}', 1),
            ('?assessment_id=not-a-uuid', None),
        ):
            response = self.client.get(f'/api/assessment-results/{query}')
            if expected is None:
                self.assertEqual(response.status_code, 400, response.data)
                continue
            self.assertEqual(response.data['count'], expected, query)

    def test_student_history_groups_marks_by_academic_semester_and_module(self):
        response = api_client(self.student).get('/api/me/academic-history/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['student']['id'], str(self.student.pk))
        year = response.data['academic_years'][0]
        semester = year['semesters'][0]
        module = semester['modules'][0]
        self.assertEqual(module['module_code'], self.plan.module.module_code)
        self.assertEqual(module['obtained_total'], '8.00')
        self.assertEqual(module['maximum_total'], '10.00')
        self.assertEqual(module['results'][0]['title'], self.quiz_one.title)

    def test_admin_can_read_a_student_history(self):
        response = self.client.get(f'/api/users/{self.student.pk}/academic-history/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['student']['id'], str(self.student.pk))


class GradingCompletionTests(MarkingTestCase):
    def test_mark_graded_refuses_a_missing_mark(self):
        self.open(self.quiz_one)
        self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        response = self.grade(self.quiz_one)

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('enrollment_id', response.data['fields'])
        self.quiz_one.refresh_from_db()
        self.assertEqual(self.quiz_one.status, Assessment.Status.OPEN)

    def test_dropped_students_are_not_required(self):
        self.open(self.quiz_one)
        self.second_enrollment.status = Enrollment.Status.DROPPED
        self.second_enrollment.save()
        self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        response = self.grade(self.quiz_one)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'GRADED')

    def test_partial_grading_keeps_the_distribution_pending(self):
        for assessment in (self.quiz_one, self.quiz_two):
            self.open(assessment)
            for enrollment in (self.enrollment, self.second_enrollment):
                self.client.post(
                    '/api/assessment-results/',
                    self.payload(assessment, enrollment),
                    format='json',
                )

        response = self.grade(self.quiz_one)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['distribution_status'], 'PENDING')
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.status, AssessmentDistribution.Status.PENDING)

    def test_grading_the_last_assessment_completes_the_distribution(self):
        for assessment in (self.quiz_one, self.quiz_two):
            self.open(assessment)
            for enrollment in (self.enrollment, self.second_enrollment):
                self.client.post(
                    '/api/assessment-results/',
                    self.payload(assessment, enrollment),
                    format='json',
                )
            response = self.grade(assessment)
            self.assertEqual(response.status_code, 200, response.data)

        self.quiz.refresh_from_db()
        self.quiz_two.refresh_from_db()
        self.final.refresh_from_db()
        self.assertEqual(self.quiz.status, AssessmentDistribution.Status.COMPLETED)
        self.assertEqual(self.quiz_two.status, Assessment.Status.GRADED)
        self.assertFalse(response.data['run_finished'])

    def test_completing_the_final_distribution_finishes_the_run(self):
        assessments = [self.quiz_one, self.quiz_two, self.final_exam]
        for assessment in assessments:
            self.open(assessment)
            for enrollment in (self.enrollment, self.second_enrollment):
                self.client.post(
                    '/api/assessment-results/',
                    self.payload(assessment, enrollment, marks='9.00'),
                    format='json',
                )
            response = self.grade(assessment)
            self.assertEqual(response.status_code, 200, response.data)

        self.assertEqual(response.data['distribution_status'], 'COMPLETED')
        self.assertTrue(response.data['run_finished'])
        self.final.refresh_from_db()
        self.assertEqual(self.final.status, AssessmentDistribution.Status.COMPLETED)

    def test_grading_twice_is_conflict(self):
        self.open(self.quiz_one)
        for enrollment in (self.enrollment, self.second_enrollment):
            self.client.post(
                '/api/assessment-results/', self.payload(self.quiz_one, enrollment), format='json'
            )
        self.grade(self.quiz_one)

        response = self.grade(self.quiz_one)

        self.assertEqual(response.status_code, 409, response.data)

    def test_marks_on_a_graded_assessment_can_still_be_corrected(self):
        self.open(self.quiz_one)
        for enrollment in (self.enrollment, self.second_enrollment):
            self.client.post(
                '/api/assessment-results/', self.payload(self.quiz_one, enrollment), format='json'
            )
        self.grade(self.quiz_one)

        response = self.teacher_client.patch(
            f"/api/assessment-results/{AssessmentResult.objects.first().pk}/",
            {'marks_obtained': '7.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.quiz_one.refresh_from_db()
        self.assertEqual(self.quiz_one.status, Assessment.Status.GRADED)

    def test_a_plan_with_marks_cannot_go_back_to_draft(self):
        self.open(self.quiz_one)
        self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        response = self.client.post(
            f'/api/lecture-plans/{self.plan.pk}/revert-to-draft/', {}, format='json'
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, LecturePlan.Status.PUBLISHED)

    def test_user_delete_is_blocked_after_grading(self):
        self.open(self.quiz_one)
        self.client.post(
            '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
        )

        response = self.client.delete(f'/api/users/{self.admin.pk}/')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())


class MarkPushTests(MarkingTestCase):
    def setUp(self):
        super().setUp()
        self.open(self.quiz_one)

    def give(self, user, index):
        user.push_token = f'ExponentPushToken[student-{index}]'
        user.save(update_fields=['push_token'])
        return user.push_token

    def test_a_recorded_mark_notifies_that_student(self):
        token = self.give(self.student, 1)

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.teacher_client.post(
                '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
            )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(batches), 1)
        message = batches[0][0]
        self.assertEqual(message['to'], token)
        self.assertEqual(message['title'], 'Your mark is ready')
        self.assertIn(self.quiz_one.title, message['body'])
        self.assertIn('8.00', message['body'])
        self.assertEqual(message['data'], {'type': 'mark', 'assessment_id': str(self.quiz_one.pk)})

    def test_a_correction_notifies_again(self):
        self.give(self.student, 1)

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            created = self.client.post(
                '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
            ).data
            batches.clear()

            response = self.teacher_client.patch(
                f"/api/assessment-results/{created['id']}/", {'marks_obtained': '9.50'}, format='json'
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([message['title'] for message in batches[0]], ['Your mark is ready'])

    def test_a_bulk_run_is_one_request_holding_one_message_per_student(self):
        first = self.give(self.student, 1)
        second = self.give(self.second_student, 2)

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.teacher_client.post(
                '/api/assessment-results/bulk/',
                self.bulk_payload(
                    self.quiz_one, [(self.enrollment, '8.00'), (self.second_enrollment, '5.00')]
                ),
                format='json',
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(batches), 1)
        self.assertEqual([message['to'] for message in batches[0]], [first, second])
        self.assertEqual(
            [message['data'] for message in batches[0]],
            [{'type': 'mark', 'assessment_id': str(self.quiz_one.pk)}] * 2,
        )

    def test_a_student_without_a_token_is_left_out_of_the_bulk(self):
        second = self.give(self.second_student, 2)

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.teacher_client.post(
                '/api/assessment-results/bulk/',
                self.bulk_payload(
                    self.quiz_one, [(self.enrollment, '8.00'), (self.second_enrollment, '5.00')]
                ),
                format='json',
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([message['to'] for message in batches[0]], [second])

    def test_an_unregistered_student_gets_nothing(self):
        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.teacher_client.post(
                '/api/assessment-results/', self.payload(self.quiz_one, self.enrollment), format='json'
            )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(batches, [])

    def test_a_failed_bulk_rolls_back_and_notifies_nobody(self):
        self.give(self.student, 1)
        unknown = '00000000-0000-4000-8000-000000000000'

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.teacher_client.post(
                '/api/assessment-results/bulk/',
                {
                    'assessment_id': str(self.quiz_one.pk),
                    'results': [
                        {'enrollment_id': str(self.enrollment.pk), 'marks_obtained': '8.00'},
                        {'enrollment_id': unknown, 'marks_obtained': '7.00'},
                    ],
                },
                format='json',
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(batches, [])
