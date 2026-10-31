import uuid

from django.conf import settings
from django.db import models


class Academic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    academic_year = models.CharField(max_length=32, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return self.academic_year

    def clear_other_current(self):
        """Unset the previous current year. Callers keep both writes in one transaction."""
        Academic.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)


class Semester(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    academic = models.ForeignKey(
        Academic, on_delete=models.PROTECT, related_name='semesters'
    )
    semester_no = models.PositiveSmallIntegerField()
    semester_name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ['academic', 'semester_no']
        constraints = [
            models.UniqueConstraint(
                fields=['academic', 'semester_no'], name='unique_semester_per_academic'
            )
        ]

    def __str__(self):
        return f'{self.academic.academic_year} semester {self.semester_no}'


class Major(models.Model):
    """A programme/major used to determine which module offerings a student may take."""

    class Duration(models.IntegerChoices):
        FOUR_YEARS = 4, '4 years'
        FIVE_YEARS = 5, '5 years'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    duration_years = models.PositiveSmallIntegerField(
        choices=Duration.choices, default=Duration.FOUR_YEARS
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} - {self.name}'


class StudentPlacement(models.Model):
    """A student's immutable semester history; exactly one row is current in application logic."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='placements'
    )
    semester = models.ForeignKey(
        Semester, on_delete=models.PROTECT, related_name='student_placements'
    )
    major = models.ForeignKey(
        Major, on_delete=models.PROTECT, related_name='student_placements'
    )
    study_year = models.PositiveSmallIntegerField(default=1)
    admitted_on = models.DateField()
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-semester__academic__start_date', '-semester__semester_no']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'semester'], name='unique_student_semester_placement'
            )
        ]

    def __str__(self):
        return f'{self.student} · {self.major.code} Y{self.study_year} · {self.semester}'

    def make_current(self):
        StudentPlacement.objects.filter(student=self.student, is_current=True).exclude(
            pk=self.pk
        ).update(is_current=False)


class Module(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    semester = models.ForeignKey(
        Semester, on_delete=models.PROTECT, related_name='modules'
    )
    module_code = models.CharField(max_length=32)
    module_name = models.CharField(max_length=150)
    study_year = models.PositiveSmallIntegerField(default=1)
    eligible_majors = models.ManyToManyField(
        Major, related_name='module_offerings', blank=True
    )

    class Meta:
        ordering = ['semester', 'module_code']
        constraints = [
            models.UniqueConstraint(
                fields=['semester', 'study_year', 'module_code'],
                name='unique_code_per_semester_study_year',
            )
        ]

    def __str__(self):
        return f'{self.module_code} - {self.module_name}'


class AcademicSetupDraft(models.Model):
    """Resumable setup data that is promoted into live tables only when published."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        PUBLISHED = 'PUBLISHED', 'Published'
        DISCARDED = 'DISCARDED', 'Discarded'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='academic_setup_drafts',
    )
    current_step = models.PositiveSmallIntegerField(default=1)
    data = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.name} ({self.status})'


class Enrollment(models.Model):
    class Status(models.TextChoices):
        ENROLLED = 'ENROLLED', 'Enrolled'
        DROPPED = 'DROPPED', 'Dropped'
        COMPLETED = 'COMPLETED', 'Completed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='enrollments'
    )
    module = models.ForeignKey(
        Module, on_delete=models.PROTECT, related_name='enrollments'
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ENROLLED
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-enrolled_at']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'module'], name='unique_student_module_enrollment'
            )
        ]

    def __str__(self):
        return f'{self.student} in {self.module}'
