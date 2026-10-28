import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from catalog.models import Academic, Module


class LecturePlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        PUBLISHED = 'PUBLISHED', 'Published'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.OneToOneField(
        Module, on_delete=models.PROTECT, related_name='lecture_plan'
    )
    lecturer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lecture_plans'
    )
    academic = models.ForeignKey(
        Academic, on_delete=models.PROTECT, related_name='lecture_plans'
    )
    note = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ['module__module_code']

    def __str__(self):
        return f'{self.module.module_code} plan ({self.status})'

    def publish_errors(self):
        """Field -> messages map that blocks publish. Empty means the plan is complete."""
        distributions = [
            (distribution, list(distribution.assessments.all()))
            for distribution in self.distributions.all()
        ]
        errors = {}

        def add(field, message):
            errors.setdefault(field, []).append(message)

        if not distributions:
            add('distributions', ['The plan has no assessment type.'])

        for distribution, assessments in distributions:
            label = distribution.assessment_type
            if len(assessments) != distribution.total_assessment:
                add(
                    'total_assessment',
                    [
                        f'{label} has {len(assessments)} assessments but '
                        f'total_assessment is {distribution.total_assessment}.'
                    ],
                )
            sequences = sorted(assessment.sequence_no for assessment in assessments)
            if sequences != list(range(1, distribution.total_assessment + 1)):
                add(
                    'sequence_no',
                    [
                        f'{label} sequence numbers must run 1..{distribution.total_assessment}, '
                        f'got {sequences}.'
                    ],
                )
            for assessment in assessments:
                if assessment.max_mark <= 0:
                    add('max_mark', [f'{assessment.title} must have max_mark greater than 0.'])
                if not assessment.title:
                    add('title', [f'{label} assessment {assessment.sequence_no} needs a title.'])
        return errors

    def is_run_finished(self):
        final = self.distributions.filter(
            assessment_type=AssessmentDistribution.AssessmentType.FINAL
        ).first()
        return final is not None and final.status == AssessmentDistribution.Status.COMPLETED

    def revert_to_draft(self):
        self.status = self.Status.DRAFT
        self.save(update_fields=['status'])
        self.distributions.update(status=AssessmentDistribution.Status.PENDING)
        Assessment.objects.filter(distribution__lecture_plan=self).update(
            status=Assessment.Status.SCHEDULED
        )


class AssessmentDistribution(models.Model):
    class AssessmentType(models.TextChoices):
        QUIZ = 'QUIZ', 'Quiz'
        ASSIGNMENT = 'ASSIGNMENT', 'Assignment'
        LAB = 'LAB', 'Lab'
        PRESENTATION = 'PRESENTATION', 'Presentation'
        MIDTERM = 'MIDTERM', 'Midterm'
        FINAL = 'FINAL', 'Final'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lecture_plan = models.ForeignKey(
        LecturePlan, on_delete=models.PROTECT, related_name='distributions'
    )
    assessment_type = models.CharField(max_length=20, choices=AssessmentType.choices)
    total_assessment = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    display_order = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        ordering = ['lecture_plan', 'display_order']
        constraints = [
            models.UniqueConstraint(
                fields=['lecture_plan', 'assessment_type'],
                name='unique_type_per_lecture_plan',
            ),
            models.UniqueConstraint(
                fields=['lecture_plan', 'display_order'],
                name='unique_display_order_per_lecture_plan',
            ),
        ]

    def __str__(self):
        return f'{self.assessment_type} x{self.total_assessment} of {self.lecture_plan}'


class Assessment(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        OPEN = 'OPEN', 'Open'
        CLOSED = 'CLOSED', 'Closed'
        GRADED = 'GRADED', 'Graded'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    distribution = models.ForeignKey(
        AssessmentDistribution, on_delete=models.PROTECT, related_name='assessments'
    )
    title = models.CharField(max_length=150)
    sequence_no = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    max_mark = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(0)]
    )
    assessment_date = models.DateField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SCHEDULED
    )

    class Meta:
        ordering = ['distribution', 'sequence_no']
        constraints = [
            models.UniqueConstraint(
                fields=['distribution', 'sequence_no'],
                name='unique_sequence_per_distribution',
            )
        ]

    def __str__(self):
        return f'{self.title} ({self.status})'

    @property
    def lecture_plan(self):
        return self.distribution.lecture_plan

    @property
    def module(self):
        return self.distribution.lecture_plan.module

    @property
    def accepts_marks(self):
        return self.status in (
            self.Status.OPEN,
            self.Status.CLOSED,
            self.Status.GRADED,
        )
