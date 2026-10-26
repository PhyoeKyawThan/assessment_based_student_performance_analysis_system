import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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


class Module(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    semester = models.ForeignKey(
        Semester, on_delete=models.PROTECT, related_name='modules'
    )
    module_code = models.CharField(max_length=32)
    module_name = models.CharField(max_length=150)
    credit_hours = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(30)]
    )

    class Meta:
        ordering = ['semester', 'module_code']
        constraints = [
            models.UniqueConstraint(
                fields=['semester', 'module_code'], name='unique_code_per_semester'
            )
        ]

    def __str__(self):
        return f'{self.module_code} - {self.module_name}'


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
