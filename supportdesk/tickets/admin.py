from django.contrib import admin

from .models import (
    AuditEvent, CannedReply, InternalNote, KnowledgeArticle, Notification,
    SatisfactionRating, SavedView, Ticket, TicketAttachment, TicketPresence,
    TicketTag, UserPreferences,
)

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "priority",
        "status",
        "created_by",
        "assigned_to",
        "created_at",
    )

    list_filter = ("priority", "status")
    search_fields = ("title", "description")


admin.site.register(TicketTag)
admin.site.register(InternalNote)
admin.site.register(AuditEvent)
admin.site.register(Notification)
admin.site.register(UserPreferences)
admin.site.register(SavedView)
admin.site.register(CannedReply)
admin.site.register(TicketAttachment)
admin.site.register(KnowledgeArticle)
admin.site.register(SatisfactionRating)
admin.site.register(TicketPresence)
