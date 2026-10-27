import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from catalog.models import Enrollment
from teaching.models import Assessment


class AssessmentResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(
        Assessment, on_delete=models.PROTECT, related_name='results'
    )
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='results'
    )
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='graded_results',
    )
    graded_at = models.DateTimeField(default=timezone.now)
    remark = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-graded_at']
        constraints = [
            models.UniqueConstraint(
                fields=['assessment', 'enrollment'],
                name='unique_result_per_assessment_enrollment',
            )
        ]

    def __str__(self):
        return f'{self.marks_obtained} for {self.enrollment} in {self.assessment}'
