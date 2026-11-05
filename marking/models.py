import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from catalog.models import Enrollment
from teaching.models import Assessment, LecturePlan


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


class ModuleResultSheet(models.Model):
    """The official raw-mark sheet and approval state for one module offering."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        RETURNED = 'RETURNED', 'Returned for revision'
        VERIFIED = 'VERIFIED', 'Verified'
        LOCKED = 'LOCKED', 'Locked and published'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lecture_plan = models.OneToOneField(
        LecturePlan, on_delete=models.PROTECT, related_name='result_sheet'
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='submitted_result_sheets',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='reviewed_result_sheets',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    returned_comment = models.TextField(blank=True, default='')
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='locked_result_sheets',
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-lecture_plan__academic__start_date', 'lecture_plan__module__module_code']

    def __str__(self):
        return f'{self.lecture_plan.module.module_code} results ({self.status})'


class ModulePeriodResult(models.Model):
    """One student's official raw mark in a module period."""

    class Source(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual entry'
        SPREADSHEET = 'SPREADSHEET', 'Spreadsheet import'
        COMPONENTS = 'COMPONENTS', 'Assessment components'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sheet = models.ForeignKey(
        ModuleResultSheet, on_delete=models.CASCADE, related_name='results'
    )
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='period_results'
    )
    raw_mark = models.DecimalField(max_digits=7, decimal_places=2)
    maximum_mark = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    remark = models.TextField(blank=True, default='')
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='entered_period_results'
    )
    entered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['enrollment__student__full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['sheet', 'enrollment'], name='unique_period_result_per_enrollment'
            )
        ]


class ResultWorkflowEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sheet = models.ForeignKey(
        ModuleResultSheet, on_delete=models.CASCADE, related_name='workflow_events'
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='result_workflow_events'
    )
    from_status = models.CharField(max_length=20, choices=ModuleResultSheet.Status.choices)
    to_status = models.CharField(max_length=20, choices=ModuleResultSheet.Status.choices)
    comment = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
