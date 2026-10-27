"""Push messages the marking app owns."""

from catalog.models import Enrollment
from core.notifications import send_personalized


def notify_marks_recorded(results) -> int:
    """Tell every marked student their result is ready, one batched push for the whole run."""
    students = {
        enrollment.pk: enrollment.student
        for enrollment in Enrollment.objects.filter(
            pk__in={result.enrollment_id for result in results}
        ).select_related('student')
    }

    messages = [
        (
            students[result.enrollment_id],
            'Your mark is ready',
            f'{result.assessment.module.module_code} · {result.assessment.title}: '
            f'{result.marks_obtained} of {result.assessment.max_mark}',
            {'type': 'mark', 'assessment_id': str(result.assessment_id)},
        )
        for result in results
    ]
    return send_personalized(messages)
