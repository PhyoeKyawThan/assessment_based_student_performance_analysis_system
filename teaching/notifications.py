"""Push messages the teaching app owns."""

from catalog.models import Enrollment
from core.notifications import send_push
from users.models import User

ACTIVE_STATUSES = (Enrollment.Status.ENROLLED, Enrollment.Status.COMPLETED)


def notify_assessment_opened(assessment) -> int:
    """Tell everyone still enrolled that an assessment is now open."""
    students = User.objects.filter(
        enrollments__module=assessment.module,
        enrollments__status__in=ACTIVE_STATUSES,
        push_token__gt='',
    ).distinct()

    return send_push(
        students,
        'Assessment is open',
        f'{assessment.module.module_code} · {assessment.title} is now open for marks.',
        data={'type': 'assessment', 'assessment_id': str(assessment.pk)},
    )
