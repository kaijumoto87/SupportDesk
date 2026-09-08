from django.conf import settings
from django.db import models
from django.utils import timezone

class Ticket(models.Model):
    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    title = models.CharField(max_length=200)
    description = models.TextField()

    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_tickets",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_tickets",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    chat_archived = models.BooleanField(default=False)
    category = models.CharField(max_length=80, blank=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged_tickets")
    tags = models.ManyToManyField("TicketTag", blank=True, related_name="tickets")

    def __str__(self):
        return self.title

    class Meta:
        permissions = [
            ("manage_workflow", "Can manage ticket workflow"),
            ("delete_chats", "Can delete complete chats"),
            ("view_audit_log", "Can view the audit log"),
            ("manage_knowledge", "Can manage knowledge articles"),
        ]


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ticket_messages",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.ticket_id}: {self.author}"


class PlatformSettings(models.Model):
    site_name = models.CharField(max_length=80, default="SupportDesk")
    support_email = models.EmailField(default="support@example.com")
    allow_customer_signup = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "platform settings"

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

    def __str__(self):
        return self.site_name


class DirectMessage(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_direct_messages")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_direct_messages")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.sender} → {self.recipient}"


class DirectConversation(models.Model):
    participant_low = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="direct_conversations_low")
    participant_high = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="direct_conversations_high")
    archived = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["participant_low", "participant_high"], name="unique_direct_conversation")]

    @classmethod
    def for_users(cls, first, second):
        low, high = sorted((first, second), key=lambda user: user.pk)
        conversation, _ = cls.objects.get_or_create(participant_low=low, participant_high=high)
        return conversation


class TicketTag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(max_length=7, default="#9b7cff")

    def __str__(self):
        return self.name


class InternalNote(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="internal_notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    target_type = models.CharField(max_length=50)
    target_id = models.CharField(max_length=80, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_notifications")
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=240, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class UserPreferences(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_preferences")
    email_new_message = models.BooleanField(default=True)
    email_assignment = models.BooleanField(default=True)
    browser_notifications = models.BooleanField(default=True)
    available = models.BooleanField(default=True)
    two_factor_enabled = models.BooleanField(default=False)
    totp_secret = models.CharField(max_length=64, blank=True)
    email_verified = models.BooleanField(default=False)


class SavedView(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_ticket_views")
    name = models.CharField(max_length=80)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class CannedReply(models.Model):
    title = models.CharField(max_length=100)
    body = models.TextField()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    shared = models.BooleanField(default=False)


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    file = models.FileField(upload_to="ticket-attachments/%Y/%m/")
    original_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)


class KnowledgeArticle(models.Model):
    title = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    summary = models.CharField(max_length=300)
    content = models.TextField()
    keywords = models.CharField(max_length=300, blank=True)
    published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class SatisfactionRating(models.Model):
    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name="rating")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class TicketPresence(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="active_viewers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ticket", "user"], name="unique_ticket_viewer")]
