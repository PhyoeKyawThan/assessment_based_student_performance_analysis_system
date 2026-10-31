from django.test import TestCase

from catalog.models import Enrollment
from catalog.tests import CatalogFixtureMixin
from core.testing import api_client, make_user, mock_push_transport
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from users.models import User

DISTRIBUTION_PAYLOAD = {
    'assessment_type': 'QUIZ',
    'total_assessment': 1,
    'display_order': 1,
}

ASSESSMENT_PAYLOAD = {
    'title': 'Quiz 1',
    'sequence_no': 1,
    'max_mark': '10.00',
}


class TeachingFixtureMixin(CatalogFixtureMixin):
    def make_module(self, semester=None, **overrides):
        """Extra modules share one semester and get unique codes, so nothing collides."""
        if semester is None:
            if getattr(self, '_semester', None) is None:
                self._semester = self.make_semester()
            semester = self._semester
        self._module_index = getattr(self, '_module_index', 0) + 1
        overrides.setdefault('module_code', f'M{self._module_index:03d}')
        return super().make_module(semester=semester, **overrides)

    def make_plan(self, module=None, lecturer=None, **overrides):
        module = module or self.make_module()
        return LecturePlan.objects.create(
            module=module,
            lecturer=lecturer or make_user(role=User.Role.TEACHER),
            academic=module.semester.academic,
            **overrides,
        )

    def make_distribution(self, plan=None, **overrides):
        return AssessmentDistribution.objects.create(
            lecture_plan=plan or self.make_plan(), **{**DISTRIBUTION_PAYLOAD, **overrides}
        )

    def make_assessment(self, distribution=None, **overrides):
        return Assessment.objects.create(
            distribution=distribution or self.make_distribution(),
            **{**ASSESSMENT_PAYLOAD, **overrides},
        )

    def make_publishable_plan(self, **overrides):
        plan = self.make_plan(**overrides)
        quiz = self.make_distribution(
            plan=plan, assessment_type='QUIZ', total_assessment=2, display_order=1
        )
        for sequence in (1, 2):
            self.make_assessment(
                distribution=quiz, title=f'Quiz {sequence}', sequence_no=sequence
            )
        final = self.make_distribution(
            plan=plan, assessment_type='FINAL', total_assessment=1, display_order=2
        )
        self.make_assessment(
            distribution=final, title='Final exam', sequence_no=1, max_mark='100.00'
        )
        return plan

    def distribution_payload(self, plan, **overrides):
        return {
            'lecture_plan_id': str(plan.pk),
            **DISTRIBUTION_PAYLOAD,
            'assessment_type': 'ASSIGNMENT',
            'display_order': 2,
            **overrides,
        }

    def assessment_payload(self, distribution, **overrides):
        return {
            'assessment_distribution_id': str(distribution.pk),
            **ASSESSMENT_PAYLOAD,
            **overrides,
        }


class LecturePlanTests(TeachingFixtureMixin, TestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.other_teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)
        self.module = self.make_module()

    def create(self, **overrides):
        return self.client.post(
            '/api/lecture-plans/',
            {
                'module_id': str(self.module.pk),
                'lecturer_id': str(self.teacher.pk),
                'note': 'Weekly quiz, one midterm, one final.',
                **overrides,
            },
            format='json',
        )

    def test_admin_create_sets_academic_from_the_module(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'DRAFT')
        self.assertEqual(response.data['academic_id'], str(self.module.semester.academic_id))
        self.assertEqual(response.data['academic_year'], '2026-2027')
        self.assertEqual(response.data['semester']['id'], str(self.module.semester_id))
        self.assertEqual(response.data['assessment_total'], 0)
        self.assertEqual(response.data['total_marks'], '0.00')
        self.assertEqual(
            set(response.data['module']),
            {'id', 'module_code', 'module_name', 'study_year'},
        )
        self.assertEqual(set(response.data['semester']), {'id', 'semester_no', 'semester_name'})
        self.assertEqual(set(response.data['lecturer']), {'id', 'full_name', 'email'})

    def test_only_an_admin_may_offer_a_module(self):
        response = api_client(self.teacher).post(
            '/api/lecture-plans/',
            {'module_id': str(self.module.pk), 'lecturer_id': str(self.teacher.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data['code'], 'permission_denied')
        self.assertEqual(LecturePlan.objects.count(), 0)

    def test_a_module_of_a_past_academic_year_cannot_be_offered(self):
        past = self.make_academic(
            academic_year='2025-2026', is_current=False,
            start_date='2025-12-01', end_date='2026-09-30',
        )
        module = self.make_module(semester=self.make_semester(academic=past))

        response = self.client.post(
            '/api/lecture-plans/',
            {'module_id': str(module.pk), 'lecturer_id': str(self.teacher.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('module_id', response.data['fields'])

    def test_lecturer_id_is_required(self):
        response = self.create(lecturer_id=None)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('lecturer_id', response.data['fields'])

    def test_lecturer_must_be_a_teacher(self):
        response = self.create(lecturer_id=str(self.student.pk))

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('lecturer_id', response.data['fields'])

    def test_second_plan_for_a_module_is_conflict(self):
        self.create()

        response = self.create(lecturer_id=str(self.other_teacher.pk))

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'conflict')
        self.assertEqual(LecturePlan.objects.count(), 1)

    def test_student_cannot_write(self):
        response = api_client(self.student).post(
            '/api/lecture-plans/', {'module_id': str(self.module.pk)}, format='json'
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data['code'], 'permission_denied')

    def test_visibility_per_role(self):
        mine = self.make_plan(module=self.module, lecturer=self.teacher)
        theirs = self.make_plan(
            module=self.make_module(module_code='CS202'),
            lecturer=self.other_teacher,
            status=LecturePlan.Status.PUBLISHED,
        )
        draft = self.make_plan(
            module=self.make_module(module_code='CS303'), lecturer=self.other_teacher
        )
        Enrollment.objects.create(student=self.student, module=theirs.module)
        Enrollment.objects.create(student=self.student, module=draft.module)

        teacher_rows = api_client(self.teacher).get('/api/lecture-plans/').data['results']
        self.assertEqual([row['id'] for row in teacher_rows], [str(mine.pk)])

        student_rows = api_client(self.student).get('/api/lecture-plans/').data['results']
        self.assertEqual([row['id'] for row in student_rows], [str(theirs.pk)])

        self.assertEqual(self.client.get('/api/lecture-plans/').data['count'], 3)

    def test_student_cannot_read_a_draft_plan_detail(self):
        plan = self.make_plan(lecturer=self.teacher)
        Enrollment.objects.create(student=self.student, module=plan.module)

        response = api_client(self.student).get(f'/api/lecture-plans/{plan.pk}/')

        self.assertEqual(response.status_code, 404, response.data)

    def test_filters(self):
        mine = self.make_plan(module=self.module, lecturer=self.teacher)
        theirs = self.make_plan(
            module=self.make_module(module_code='CS202'),
            lecturer=self.other_teacher,
            status=LecturePlan.Status.PUBLISHED,
        )

        for query, expected in (
            (f'?module_id={mine.module_id}', [mine.pk]),
            (f'?lecturer_id={theirs.lecturer_id}', [theirs.pk]),
            (f'?academic_id={mine.academic_id}', [mine.pk, theirs.pk]),
            (f'?semester_id={mine.module.semester_id}', [mine.pk, theirs.pk]),
            ('?status=PUBLISHED', [theirs.pk]),
        ):
            rows = self.client.get(f'/api/lecture-plans/{query}').data['results']
            self.assertEqual(
                sorted(row['id'] for row in rows), sorted(str(pk) for pk in expected), query
            )

    def test_malformed_filter_is_400(self):
        response = self.client.get('/api/lecture-plans/?lecturer_id=not-a-uuid')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('lecturer_id', response.data['fields'])

    def test_owner_patches_note_only(self):
        plan = self.make_plan(lecturer=self.teacher)

        ok = api_client(self.teacher).patch(
            f'/api/lecture-plans/{plan.pk}/', {'note': 'Updated'}, format='json'
        )
        blocked = api_client(self.teacher).patch(
            f'/api/lecture-plans/{plan.pk}/',
            {'lecturer_id': str(self.other_teacher.pk)},
            format='json',
        )

        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data['note'], 'Updated')
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self.assertIn('lecturer_id', blocked.data['fields'])

    def test_admin_moves_a_draft_plan_to_another_teacher(self):
        plan = self.make_plan(lecturer=self.teacher)

        response = self.client.patch(
            f'/api/lecture-plans/{plan.pk}/',
            {'lecturer_id': str(self.other_teacher.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['lecturer']['id'], str(self.other_teacher.pk))

    def test_lecturer_and_module_are_frozen_after_publish(self):
        plan = self.make_plan(lecturer=self.teacher, status=LecturePlan.Status.PUBLISHED)

        lecturer = self.client.patch(
            f'/api/lecture-plans/{plan.pk}/',
            {'lecturer_id': str(self.other_teacher.pk)},
            format='json',
        )
        module = self.client.patch(
            f'/api/lecture-plans/{plan.pk}/',
            {'module_id': str(self.make_module(module_code='CS999').pk)},
            format='json',
        )

        self.assertEqual(lecturer.status_code, 409, lecturer.data)
        self.assertEqual(module.status_code, 400, module.data)
        self.assertIn('module_id', module.data['fields'])

    def test_delete_draft_removes_its_scheme(self):
        plan = self.make_publishable_plan(lecturer=self.teacher)

        response = self.client.delete(f'/api/lecture-plans/{plan.pk}/')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(LecturePlan.objects.count(), 0)
        self.assertEqual(AssessmentDistribution.objects.count(), 0)
        self.assertEqual(Assessment.objects.count(), 0)

    def test_delete_published_plan_is_conflict(self):
        plan = self.make_plan(status=LecturePlan.Status.PUBLISHED)

        response = self.client.delete(f'/api/lecture-plans/{plan.pk}/')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(LecturePlan.objects.filter(pk=plan.pk).exists())

    def test_other_teacher_cannot_reach_the_plan(self):
        plan = self.make_plan(lecturer=self.teacher)
        client = api_client(self.other_teacher)

        for method in (client.get, client.delete):
            response = method(f'/api/lecture-plans/{plan.pk}/')
            self.assertEqual(response.status_code, 404, response.data)

    def test_me_lecture_plans_is_teacher_only(self):
        self.make_plan(lecturer=self.teacher, status=LecturePlan.Status.PUBLISHED)
        self.make_plan(lecturer=self.teacher)

        rows = api_client(self.teacher).get('/api/me/lecture-plans/').data
        self.assertEqual(rows['count'], 2)
        self.assertEqual(
            api_client(self.student).get('/api/me/lecture-plans/').status_code, 403
        )
        filtered = api_client(self.teacher).get('/api/me/lecture-plans/?status=DRAFT').data
        self.assertEqual(filtered['count'], 1)

    def test_publish_happy_path(self):
        plan = self.make_publishable_plan(lecturer=self.teacher)

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'PUBLISHED')
        self.assertEqual(response.data['assessment_total'], 3)
        self.assertEqual(response.data['total_marks'], '120.00')

    def test_publish_needs_at_least_one_distribution(self):
        plan = self.make_plan(lecturer=self.teacher)

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('distributions', response.data['fields'])
        self.assertEqual(LecturePlan.objects.get(pk=plan.pk).status, LecturePlan.Status.DRAFT)

    def test_publish_needs_no_weighting_and_accepts_any_mark_total(self):
        plan = self.make_plan(lecturer=self.teacher)
        distribution = self.make_distribution(plan=plan)
        self.make_assessment(distribution=distribution, max_mark='7.50')

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total_marks'], '7.50')

    def test_publish_needs_each_type_to_be_complete(self):
        plan = self.make_plan(lecturer=self.teacher)
        self.make_distribution(plan=plan, total_assessment=2)
        self.make_assessment(distribution=plan.distributions.first(), title='Quiz 4', sequence_no=4)

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(
            response.data['fields']['total_assessment'],
            ['QUIZ has 1 assessments but total_assessment is 2.'],
        )
        self.assertIn('sequence_no', response.data['fields'])

    def test_publish_needs_positive_max_mark(self):
        plan = self.make_plan(lecturer=self.teacher)
        distribution = self.make_distribution(plan=plan)
        self.make_assessment(distribution=distribution, max_mark='0.00')

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('max_mark', response.data['fields'])

    def test_publish_twice_is_conflict(self):
        plan = self.make_publishable_plan(lecturer=self.teacher)
        self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')

        self.assertEqual(response.status_code, 409, response.data)

    def test_other_teacher_cannot_publish(self):
        plan = self.make_publishable_plan(lecturer=self.teacher)

        response = api_client(self.other_teacher).post(
            f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json'
        )

        self.assertEqual(response.status_code, 404, response.data)

    def test_owner_can_publish(self):
        plan = self.make_publishable_plan(lecturer=self.teacher)

        response = api_client(self.teacher).post(
            f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'PUBLISHED')

    def test_revert_to_draft_resets_children(self):
        plan = self.make_publishable_plan(lecturer=self.teacher)
        self.client.post(f'/api/lecture-plans/{plan.pk}/publish/', {}, format='json')
        self.client.post(
            f"/api/assessments/{plan.distributions.first().assessments.first().pk}/open/",
            {},
            format='json',
        )

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/revert-to-draft/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'DRAFT')
        plan.refresh_from_db()
        self.assertEqual(
            list(plan.distributions.values_list('status', flat=True)), ['PENDING', 'PENDING']
        )
        self.assertEqual(
            set(Assessment.objects.filter(distribution__lecture_plan=plan).values_list(
                'status', flat=True
            )),
            {Assessment.Status.SCHEDULED},
        )

    def test_revert_a_draft_plan_is_conflict(self):
        plan = self.make_plan(lecturer=self.teacher)

        response = self.client.post(f'/api/lecture-plans/{plan.pk}/revert-to-draft/', {}, format='json')

        self.assertEqual(response.status_code, 409, response.data)


class AssessmentDistributionTests(TeachingFixtureMixin, TestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.other_teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)
        self.plan = self.make_plan(lecturer=self.teacher)

    def create(self, **overrides):
        return self.client.post(
            '/api/assessment-distributions/',
            self.distribution_payload(self.plan, **overrides),
            format='json',
        )

    def test_create(self):
        self.make_distribution(plan=self.plan, assessment_type='QUIZ', display_order=1)

        response = self.create(assessment_type='ASSIGNMENT')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertEqual(response.data['assessment_count'], 0)
        self.assertEqual(response.data['total_marks'], '0.00')
        self.assertEqual(str(response.data['lecture_plan_id']), str(self.plan.pk))

    def test_a_percentage_in_the_payload_is_ignored(self):
        response = self.create(assessment_type='LAB', display_order=5, percentage=40)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('percentage', response.data)

    def test_duplicate_type_is_conflict(self):
        self.make_distribution(plan=self.plan, assessment_type='ASSIGNMENT')

        response = self.create(assessment_type='ASSIGNMENT', display_order=3)

        self.assertEqual(response.status_code, 409, response.data)

    def test_duplicate_display_order_is_conflict(self):
        self.make_distribution(plan=self.plan, assessment_type='ASSIGNMENT', display_order=2)

        response = self.create(display_order=2)

        self.assertEqual(response.status_code, 409, response.data)

    def test_counts_must_be_positive(self):
        for field, value in (('total_assessment', 0), ('display_order', 0)):
            response = self.create(**{field: value, 'assessment_type': 'LAB'})
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn(field, response.data['fields'])

    def test_adding_to_another_teachers_plan_is_forbidden(self):
        foreign = self.make_plan(
            module=self.make_module(module_code='CS202'), lecturer=self.other_teacher
        )

        response = api_client(self.teacher).post(
            '/api/assessment-distributions/',
            self.distribution_payload(foreign),
            format='json',
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_published_plan_blocks_writes(self):
        distribution = self.make_distribution(plan=self.plan)
        self.make_assessment(distribution=distribution)
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')

        create = self.create(assessment_type='LAB', display_order=3)
        patch = self.client.patch(
            f'/api/assessment-distributions/{distribution.pk}/',
            {'total_assessment': 5},
            format='json',
        )
        delete = self.client.delete(f'/api/assessment-distributions/{distribution.pk}/')

        for response in (create, patch, delete):
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(AssessmentDistribution.objects.count(), 1)

    def test_filter_by_lecture_plan(self):
        self.make_distribution(plan=self.plan, assessment_type='QUIZ')
        other = self.make_plan(
            module=self.make_module(module_code='CS202'), lecturer=self.other_teacher
        )
        self.make_distribution(plan=other, assessment_type='QUIZ')

        rows = self.client.get(
            f'/api/assessment-distributions/?lecture_plan_id={self.plan.pk}'
        ).data['results']

        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]['lecture_plan_id']), str(self.plan.pk))

    def test_delete_removes_child_assessments(self):
        distribution = self.make_distribution(plan=self.plan)
        self.make_assessment(distribution=distribution)

        response = self.client.delete(f'/api/assessment-distributions/{distribution.pk}/')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(Assessment.objects.count(), 0)

    def test_student_cannot_write(self):
        response = api_client(self.student).post(
            '/api/assessment-distributions/',
            self.distribution_payload(self.plan),
            format='json',
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_visibility_follows_the_plan(self):
        self.make_distribution(plan=self.plan, assessment_type='QUIZ')
        rows = api_client(self.teacher).get('/api/assessment-distributions/').data
        self.assertEqual(rows['count'], 1)
        self.assertEqual(api_client(self.student).get('/api/assessment-distributions/').data['count'], 0)


class AssessmentTests(TeachingFixtureMixin, TestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.other_teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)
        self.plan = self.make_plan(lecturer=self.teacher)
        self.distribution = self.make_distribution(plan=self.plan)

    def create(self, distribution=None, **overrides):
        return self.client.post(
            '/api/assessments/',
            self.assessment_payload(distribution or self.distribution, **overrides),
            format='json',
        )

    def test_create_sets_scheduled(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'SCHEDULED')
        self.assertEqual(response.data['lecture_plan_id'], str(self.plan.pk))
        self.assertEqual(response.data['module_id'], str(self.plan.module_id))
        self.assertEqual(response.data['assessment_type'], 'QUIZ')
        self.assertEqual(response.data['distribution_status'], 'PENDING')

    def test_duplicate_sequence_is_conflict(self):
        self.make_assessment(distribution=self.distribution, sequence_no=1)

        response = self.create(sequence_no=1, title='Quiz 2')

        self.assertEqual(response.status_code, 409, response.data)

    def test_create_after_publish_is_conflict(self):
        self.make_assessment(distribution=self.distribution)
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')

        response = self.create(sequence_no=2, title='Quiz 2')

        self.assertEqual(response.status_code, 409, response.data)

    def test_adding_to_another_teachers_plan_is_forbidden(self):
        foreign = self.make_plan(
            module=self.make_module(module_code='CS202'), lecturer=self.other_teacher
        )
        distribution = self.make_distribution(plan=foreign)

        response = api_client(self.teacher).post(
            '/api/assessments/', self.assessment_payload(distribution), format='json'
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_patch_while_draft_changes_every_field_but_status(self):
        assessment = self.make_assessment(distribution=self.distribution)

        response = self.client.patch(
            f'/api/assessments/{assessment.pk}/',
            {
                'title': 'Quiz one',
                'sequence_no': 2,
                'max_mark': '15.00',
                'assessment_date': '2027-02-02',
                'status': 'GRADED',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['title'], 'Quiz one')
        self.assertEqual(str(response.data['max_mark']), '15.00')
        self.assertEqual(response.data['sequence_no'], 2)
        self.assertEqual(response.data['status'], 'SCHEDULED')

    def test_parent_distribution_is_fixed(self):
        assessment = self.make_assessment(distribution=self.distribution)
        other = self.make_distribution(plan=self.plan, assessment_type='LAB', display_order=9)

        response = self.client.patch(
            f'/api/assessments/{assessment.pk}/',
            {'assessment_distribution_id': str(other.pk)},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('assessment_distribution_id', response.data['fields'])

    def test_published_plan_freezes_marks_but_allows_details(self):
        assessment = self.make_assessment(distribution=self.distribution)
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')

        frozen = self.client.patch(
            f'/api/assessments/{assessment.pk}/', {'max_mark': '20.00'}, format='json'
        )
        allowed = self.client.patch(
            f'/api/assessments/{assessment.pk}/', {'title': 'Quiz A'}, format='json'
        )

        self.assertEqual(frozen.status_code, 409, frozen.data)
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data['title'], 'Quiz A')

    def test_open_and_close(self):
        assessment = self.make_assessment(distribution=self.distribution)
        url = f'/api/assessments/{assessment.pk}'

        early = self.client.post(f'{url}/open/', {}, format='json')
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')
        opened = self.client.post(f'{url}/open/', {}, format='json')
        again = self.client.post(f'{url}/open/', {}, format='json')
        closed = self.client.post(f'{url}/close/', {}, format='json')

        self.assertEqual(early.status_code, 409, early.data)
        self.assertEqual(opened.status_code, 200, opened.data)
        self.assertEqual(opened.data['status'], 'OPEN')
        self.assertEqual(again.status_code, 409, again.data)
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data['status'], 'CLOSED')

    def test_close_requires_open(self):
        assessment = self.make_assessment(distribution=self.distribution)
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')

        response = self.client.post(f'/api/assessments/{assessment.pk}/close/', {}, format='json')

        self.assertEqual(response.status_code, 409, response.data)

    def test_delete_while_draft(self):
        assessment = self.make_assessment(distribution=self.distribution)

        response = self.client.delete(f'/api/assessments/{assessment.pk}/')

        self.assertEqual(response.status_code, 204)

    def test_delete_after_publish_is_conflict(self):
        assessment = self.make_assessment(distribution=self.distribution)
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')

        response = self.client.delete(f'/api/assessments/{assessment.pk}/')

        self.assertEqual(response.status_code, 409, response.data)

    def test_filters(self):
        other_plan = self.make_plan(
            module=self.make_module(module_code='CS202'), lecturer=self.other_teacher
        )
        other_distribution = self.make_distribution(plan=other_plan)
        first = self.make_assessment(distribution=self.distribution, sequence_no=1)
        second = self.make_assessment(distribution=other_distribution, sequence_no=1)

        for query, expected in (
            (f'?assessment_distribution_id={self.distribution.pk}', [first]),
            (f'?lecture_plan_id={other_plan.pk}', [second]),
            (f'?module_id={other_plan.module_id}', [second]),
            ('?status=SCHEDULED', [first, second]),
            ('?status=GRADED', []),
        ):
            rows = self.client.get(f'/api/assessments/{query}').data['results']
            self.assertEqual(
                sorted(row['id'] for row in rows), sorted(str(a.pk) for a in expected), query
            )

    def test_visibility_follows_the_plan(self):
        assessment = self.make_assessment(distribution=self.distribution)
        self.client.post(f'/api/lecture-plans/{self.plan.pk}/publish/', {}, format='json')

        self.assertEqual(api_client(self.teacher).get('/api/assessments/').data['count'], 1)
        self.assertEqual(api_client(self.student).get('/api/assessments/').data['count'], 0)
        Enrollment.objects.create(student=self.student, module=self.plan.module)
        self.assertEqual(api_client(self.student).get('/api/assessments/').data['count'], 1)
        self.assertEqual(
            api_client(self.student).get(f'/api/assessments/{assessment.pk}/').status_code, 200
        )


class AssessmentOpenPushTests(TeachingFixtureMixin, TestCase):
    def setUp(self):
        self.teacher = make_user(role=User.Role.TEACHER)
        self.admin = make_user(role=User.Role.ADMIN)
        self.client = api_client(self.admin)
        self.plan = self.make_plan(lecturer=self.teacher, status=LecturePlan.Status.PUBLISHED)
        self.distribution = self.make_distribution(plan=self.plan)
        self.assessment = self.make_assessment(distribution=self.distribution)

    def enroll(self, index, status=Enrollment.Status.ENROLLED, registered=True):
        student = make_user(role=User.Role.STUDENT, full_name=f'Student {index}')
        if registered:
            student.push_token = f'ExponentPushToken[student-{index}]'
            student.save(update_fields=['push_token'])
        enrollment = Enrollment.objects.create(
            student=student, module=self.plan.module, status=status
        )
        return enrollment, student

    def post_open(self):
        return self.client.post(f'/api/assessments/{self.assessment.pk}/open/', {}, format='json')

    def test_opening_an_assessment_tells_the_enrolled_students(self):
        _enrollment, student = self.enroll(1)

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.post_open()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(batches), 1)
        self.assertEqual(
            batches[0][0],
            {
                'to': student.push_token,
                'title': 'Assessment is open',
                'body': f'{self.plan.module.module_code} · {self.assessment.title} is now open for marks.',
                'data': {'type': 'assessment', 'assessment_id': str(self.assessment.pk)},
            },
        )

    def test_only_registered_and_active_students_are_reached(self):
        _kept, kept_student = self.enroll(1)
        self.enroll(2, registered=False)
        self.enroll(3, status=Enrollment.Status.DROPPED)

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.post_open()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([message['to'] for message in batches[0]], [kept_student.push_token])

    def test_a_refused_open_sends_nothing(self):
        self.enroll(1)
        self.assessment.status = Assessment.Status.GRADED
        self.assessment.save(update_fields=['status'])

        with self.settings(EXPO_ACCESS_TOKEN='expo-server-token'), mock_push_transport() as batches:
            response = self.post_open()

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(batches, [])
