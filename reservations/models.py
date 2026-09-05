from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse


class User(AbstractUser):
    display_name = models.CharField(max_length=150)
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    REQUIRED_FIELDS = ["display_name"]

    def __str__(self):
        return self.display_name or self.username

    @property
    def avatar_url(self):
        if not self.avatar:
            return ""
        return reverse("user-avatar", args=[self.pk])


class System(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#2563eb")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserNote(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="lab_note")
    content = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Note by {self.user}"


class Reservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    class Recurrence(models.TextChoices):
        NONE = "none", "Does not repeat"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    system = models.ForeignKey(System, on_delete=models.PROTECT, related_name="reservations")
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reservations")
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(db_index=True)
    purpose = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)
    series_id = models.UUIDField(null=True, blank=True, db_index=True)
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices, default=Recurrence.NONE)
    recurrence_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="cancelled_reservations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start"]
        indexes = [models.Index(fields=["system", "status", "start", "end"])]

    def clean(self):
        if self.start and self.end and self.end <= self.start:
            raise ValidationError({"end": "End time must be after start time."})

    def __str__(self):
        return f"{self.system} — {self.owner} ({self.start:%Y-%m-%d %H:%M})"
