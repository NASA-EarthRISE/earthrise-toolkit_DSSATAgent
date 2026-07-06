import uuid
from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """Extended profile data attached to each Django User via OneToOne."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    organization = models.CharField(max_length=200, blank=True, default="")
    title = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Job title",
    )
    phone = models.CharField(max_length=30, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    bio = models.TextField(blank=True, default="")
    areas_of_interest = models.TextField(
        blank=True, default="",
        help_text="Free-text tags: topics, regions, specializations",
    )
    preferred_language = models.CharField(
        max_length=10, default='en',
        help_text="ISO 639-1 language code",
    )

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='approved_users',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_userprofile'

    def __str__(self):
        return f"Profile: {self.user.get_full_name() or self.user.username}"


class RegistrationRequest(models.Model):
    """Tracks account registration requests awaiting admin approval."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='registration_request',
        help_text="The User created with is_active=False until approved",
    )
    organization = models.CharField(max_length=200, blank=True, default="")
    reason = models.TextField(
        blank=True, default="",
        help_text="Why the user is requesting access",
    )
    requested_roles = models.JSONField(
        default=list, blank=True,
        help_text="List of group names the user requested",
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='pending',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    token = models.UUIDField(
        default=uuid.uuid4, unique=True,
        help_text="Token for email verification link",
    )

    class Meta:
        db_table = 'accounts_registrationrequest'
        ordering = ['-created_at']

    def __str__(self):
        return f"Registration: {self.user.email} ({self.status})"
