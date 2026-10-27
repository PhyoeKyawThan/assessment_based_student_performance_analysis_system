"""One request per role that fills the whole dashboard: tiles, charts and short lists."""

from datetime import date

from django.db.models import Count
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Academic, Enrollment, Module, Semester
from marking.models import AssessmentResult
from marking.services import percentage_of
from teaching.models import Assessment, LecturePlan
from users.models import User

LIST_LIMIT = 5


class DashboardView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user
        if user.is_superuser or user.role == User.Role.ADMIN:
            payload = admin_dashboard()
            payload['role'] = User.Role.ADMIN
        elif user.role == User.Role.TEACHER:
            payload = teacher_dashboard(user)
            payload['role'] = User.Role.TEACHER
        else:
            payload = student_dashboard(user)
            payload['role'] = User.Role.STUDENT
        return Response(payload)


def stat(key, label, value, tone):
    return {'key': key, 'label': label, 'value': str(value), 'tone': tone}


def counts(queryset, field, choices):
    """``{choice: n}`` for every choice, including the ones with no rows."""
    present = dict(queryset.values(field).annotate(n=Count('id')).values_list(field, 'n'))
    return {value: present.get(value, 0) for value, _label in choices}


def donut(title, split, choices):
    slices = [{'key': value, 'label': label, 'value': split[value]} for value, label in choices]
    return {'title': title, 'total': sum(item['value'] for item in slices), 'slices': slices}


def bars(title, series, groups):
    return {'title': title, 'series': series, 'groups': groups}


def assessment_row(assessment):
    module = assessment.distribution.lecture_plan.module
    return {
        'id': str(assessment.pk),
        'title': assessment.title,
        'type': assessment.distribution.assessment_type,
        'type_label': assessment.distribution.get_assessment_type_display(),
        'sequence_no': assessment.sequence_no,
        'max_mark': str(assessment.max_mark),
        'assessment_date': (
            assessment.assessment_date.isoformat() if assessment.assessment_date else None
        ),
        'status': assessment.status,
        'status_label': assessment.get_status_display(),
        'module_code': module.module_code,
        'module_name': module.module_name,
    }


def upcoming_assessments(queryset):
    today = date.today()
    return [
        assessment_row(assessment)
        for assessment in queryset.filter(
            assessment_date__gte=today,
            status__in=[Assessment.Status.SCHEDULED, Assessment.Status.OPEN],
        )
        .select_related(
            'distribution__lecture_plan__module', 'distribution__lecture_plan__academic'
        )
        .order_by('assessment_date', 'distribution__display_order', 'sequence_no')[:LIST_LIMIT]
    ]


def result_row(result):
    plan = result.assessment.distribution.lecture_plan
    return {
        'id': str(result.pk),
        'marks_obtained': str(result.marks_obtained),
        'max_mark': str(result.assessment.max_mark),
        'percentage': percentage_of(result.marks_obtained, result.assessment.max_mark),
        'remark': result.remark,
        'graded_at': result.graded_at.isoformat(),
        'assessment_title': result.assessment.title,
        'module_code': plan.module.module_code,
        'module_name': plan.module.module_name,
        'student': result.enrollment.student.full_name,
    }


def graded_results(queryset):
    return [
        result_row(result)
        for result in queryset.select_related(
            'assessment__distribution__lecture_plan__module',
            'enrollment__student',
        )[:LIST_LIMIT]
    ]


def admin_dashboard():
    current = Academic.objects.filter(is_current=True).first()
    enrollment_split = counts(Enrollment.objects.all(), 'status', Enrollment.Status.choices)
    semesters = (
        Semester.objects.filter(academic=current) if current else Semester.objects.none()
    )
    per_semester = [
        {
            'label': f'Semester {semester.semester_no}',
            'values': {
                'students': Enrollment.objects.filter(module__semester=semester)
                .values('student')
                .distinct()
                .count(),
                'modules': Module.objects.filter(semester=semester).count(),
            },
        }
        for semester in semesters
    ]
    return {
        'stats': [
            stat(
                'students',
                'Total students',
                User.objects.filter(role=User.Role.STUDENT).count(),
                'primary',
            ),
            stat(
                'teachers',
                'Total teachers',
                User.objects.filter(role=User.Role.TEACHER).count(),
                'success',
            ),
            stat('modules', 'Total modules', Module.objects.count(), 'accent'),
            stat(
                'academic_year',
                'Current academic year',
                current.academic_year if current else 'Not set',
                'warning',
            ),
        ],
        'donut': donut('Enrollment overview', enrollment_split, Enrollment.Status.choices),
        'bars': bars(
            'Enrolment and modules per semester',
            [
                {'key': 'students', 'label': 'Students', 'tone': 'primary'},
                {'key': 'modules', 'label': 'Modules', 'tone': 'accent'},
            ],
            per_semester,
        ),
        'recent_enrollments': [
            {
                'id': str(enrollment.pk),
                'student': enrollment.student.full_name,
                'module_code': enrollment.module.module_code,
                'module_name': enrollment.module.module_name,
                'semester': f'Semester {enrollment.module.semester.semester_no}',
                'status': enrollment.status,
                'status_label': enrollment.get_status_display(),
                'enrolled_at': enrollment.enrolled_at.isoformat(),
            }
            for enrollment in Enrollment.objects.select_related(
                'student', 'module__semester'
            )[:LIST_LIMIT]
        ],
        'recent_modules': [
            {
                'id': str(module.pk),
                'module_code': module.module_code,
                'module_name': module.module_name,
                'credit_hours': module.credit_hours,
                'semester': f'Semester {module.semester.semester_no}',
                'academic_year': module.semester.academic.academic_year,
            }
            for module in Module.objects.filter(semester__in=semesters).select_related(
                'semester__academic'
            )[:LIST_LIMIT]
        ],
    }


def teacher_dashboard(user):
    plans = LecturePlan.objects.filter(lecturer=user)
    assessments = Assessment.objects.filter(distribution__lecture_plan__lecturer=user)
    plan_split = counts(plans, 'status', LecturePlan.Status.choices)
    assessment_split = counts(assessments, 'status', Assessment.Status.choices)
    return {
        'stats': [
            stat('modules', 'My modules', plans.values('module').distinct().count(), 'primary'),
            stat(
                'draft_plans',
                'Draft plans',
                plan_split[LecturePlan.Status.DRAFT],
                'warning',
            ),
            stat(
                'published_plans',
                'Published plans',
                plan_split[LecturePlan.Status.PUBLISHED],
                'success',
            ),
            stat(
                'open_assessments',
                'Open assessments',
                assessment_split[Assessment.Status.OPEN],
                'accent',
            ),
        ],
        'donut': donut('Lecture plan status', plan_split, LecturePlan.Status.choices),
        'bars': bars(
            'Assessments by status',
            [{'key': 'count', 'label': 'Assessments', 'tone': 'primary'}],
            [
                {'label': label, 'values': {'count': assessment_split[value]}}
                for value, label in Assessment.Status.choices
            ],
        ),
        'upcoming_assessments': upcoming_assessments(assessments),
        'recent_marks': graded_results(
            AssessmentResult.objects.filter(graded_by=user).order_by('-graded_at')
        ),
    }


def student_dashboard(user):
    enrollments = Enrollment.objects.filter(student=user)
    active = enrollments.filter(status=Enrollment.Status.ENROLLED)
    module_ids = list(active.values_list('module_id', flat=True))
    assessments = Assessment.objects.filter(
        distribution__lecture_plan__module_id__in=module_ids,
        distribution__lecture_plan__status=LecturePlan.Status.PUBLISHED,
    )
    results = AssessmentResult.objects.filter(enrollment__student=user)

    per_module = {}
    for marks, max_mark, module_id, code, name in results.values_list(
        'marks_obtained',
        'assessment__max_mark',
        'assessment__distribution__lecture_plan__module_id',
        'assessment__distribution__lecture_plan__module__module_code',
        'assessment__distribution__lecture_plan__module__module_name',
    ):
        row = per_module.setdefault(
            module_id, {'module_code': code, 'module_name': name, 'marks': 0, 'max_marks': 0}
        )
        row['marks'] += marks
        row['max_marks'] += max_mark

    total = sum(row['marks'] for row in per_module.values())
    total_max = sum(row['max_marks'] for row in per_module.values())
    overall = percentage_of(total, total_max) or '0.0'
    status_split = counts(assessments, 'status', Assessment.Status.choices)

    return {
        'stats': [
            stat('modules', 'Enrolled modules', len(module_ids), 'primary'),
            stat(
                'upcoming',
                'Upcoming assessments',
                assessments.filter(
                    assessment_date__gte=date.today(),
                    status__in=[Assessment.Status.SCHEDULED, Assessment.Status.OPEN],
                ).count(),
                'warning',
            ),
            stat('marks', 'Marks recorded', results.count(), 'success'),
            stat('percentage', 'Overall percentage', f'{overall}%', 'accent'),
        ],
        'donut': donut('Assessment status', status_split, Assessment.Status.choices),
        'bars': bars(
            'Marks per module',
            [
                {'key': 'marks', 'label': 'Marks', 'tone': 'primary'},
                {'key': 'max_marks', 'label': 'Max', 'tone': 'neutral'},
            ],
            [
                {
                    'label': row['module_code'],
                    'values': {
                        'marks': str(row['marks']),
                        'max_marks': str(row['max_marks']),
                        'percentage': percentage_of(row['marks'], row['max_marks']),
                    },
                }
                for row in per_module.values()
            ],
        ),
        'upcoming_assessments': upcoming_assessments(assessments),
        'recent_marks': graded_results(results.order_by('-graded_at')),
    }
