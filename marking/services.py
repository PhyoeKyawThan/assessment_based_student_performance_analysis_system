"""Marking rules. The mark-graded action in the teaching app calls into this module."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from catalog.models import Enrollment
from core.exceptions import ConflictError
from marking.models import AssessmentResult
from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from teaching.permissions import owns_plan

ACTIVE_STATUSES = (Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED)
ONE_DECIMAL = Decimal('0.1')


def active_enrollments(assessment):
    return Enrollment.objects.filter(
        module=assessment.module, status__in=ACTIVE_STATUSES
    )


def unmarked_enrollments(assessment):
    return active_enrollments(assessment).exclude(results__assessment=assessment)


def percentage_of(marks_obtained, max_mark):
    if not max_mark:
        return None
    return str((Decimal(marks_obtained) / Decimal(max_mark) * 100).quantize(ONE_DECIMAL))


def validate_mark(user, assessment, enrollment, marks_obtained):
    """The rules one written mark has to satisfy. Runs before anything is saved."""
    plan = assessment.lecture_plan
    if not owns_plan(user, plan):
        raise PermissionDenied('Only the owning teacher or an admin may record marks.')
    if plan.status != LecturePlan.Status.PUBLISHED:
        raise ConflictError(f'Marks need a PUBLISHED lecture plan, not {plan.status}.')
    if not assessment.accepts_marks:
        raise ConflictError(
            f'Marks are not accepted while the assessment is {assessment.status}. Open it first.'
        )
    if enrollment.module_id != plan.module_id:
        raise ValidationError({'enrollment_id': ['The enrollment is not in this assessment module.']})
    if enrollment.status == Enrollment.Status.DROPPED:
        raise ConflictError('A DROPPED enrollment cannot be marked.')
    if marks_obtained > assessment.max_mark:
        raise ValidationError(
            {'marks_obtained': [f'marks_obtained must be between 0 and {assessment.max_mark}.']}
        )


def record_result(assessment, enrollment, marks_obtained, remark, grader):
    result = AssessmentResult.objects.filter(
        assessment=assessment, enrollment=enrollment
    ).first()
    if result is None:
        result = AssessmentResult(assessment=assessment, enrollment=enrollment)
    result.marks_obtained = marks_obtained
    result.remark = remark
    result.graded_by = grader
    result.graded_at = timezone.now()
    result.save()
    return result


@transaction.atomic
def mark_graded(assessment):
    """Move the assessment to GRADED and complete its distribution when every item is graded."""
    if assessment.status == Assessment.Status.GRADED:
        raise ConflictError('This assessment is already GRADED.')
    if assessment.status == Assessment.Status.SCHEDULED:
        raise ConflictError('Open the assessment before grading it.')

    missing = [str(pk) for pk in unmarked_enrollments(assessment).values_list('pk', flat=True)]
    if missing:
        raise ConflictError(
            'Every enrolled student needs a mark first.',
            fields={'enrollment_id': [f'Missing mark for {pk}.' for pk in missing]},
        )

    assessment.status = Assessment.Status.GRADED
    assessment.save(update_fields=['status'])

    distribution = assessment.distribution
    if not distribution.assessments.exclude(status=Assessment.Status.GRADED).exists():
        distribution.status = AssessmentDistribution.Status.COMPLETED
        distribution.save(update_fields=['status'])
    return assessment
