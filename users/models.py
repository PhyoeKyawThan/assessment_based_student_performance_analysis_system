from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        TEACHER = 'TEACHER', 'Teacher'
        STUDENT = 'STUDENT', 'Student'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=50, choices=Role.choices, default=Role.STUDENT)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    # One Expo push token per account: the client overwrites it on every sign-in, so a
    # reinstalled app or a new device simply replaces the value.
    push_token = models.CharField(max_length=255, blank=True, default='')

    first_name = None
    last_name = None

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'full_name']

    def __str__(self):
        return f'{self.full_name} ({self.role})'


class Notification(models.Model):
    """A durable announcement; push delivery is only an optional transport for it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sender = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='sent_notifications'
    )
    title = models.CharField(max_length=150)
    body = models.TextField(max_length=2000)
    requires_acknowledgement = models.BooleanField(default=False)
    delivery_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    recipients = models.ManyToManyField(
        User, through='NotificationRecipient', related_name='notifications'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class NotificationRecipient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name='recipient_rows'
    )
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notification_rows'
    )
    read_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-notification__created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['notification', 'recipient'], name='unique_notification_recipient'
            )
        ]

    def __str__(self):
        return f'{self.notification} → {self.recipient}'
