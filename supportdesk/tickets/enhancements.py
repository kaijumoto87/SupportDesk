import json
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.sessions.models import Session
from django.db import models
from django.core import signing
from django.core.mail import send_mail
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import AuditEvent, CannedReply, InternalNote, KnowledgeArticle, Notification, SatisfactionRating, SavedView, Ticket, TicketAttachment, TicketPresence, TicketTag, UserPreferences


staff_required = user_passes_test(lambda user: user.is_active and user.is_staff, login_url="login")


def audit(user, action, target, **detail):
    AuditEvent.objects.create(actor=user, action=action, target_type=target.__class__.__name__, target_id=str(target.pk), detail=detail)


@staff_required
@require_http_methods(["GET", "POST"])
def ticket_notes(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    if request.method == "POST":
        body = json.loads(request.body or "{}").get("body", "").strip()
        if not body:
            return JsonResponse({"error": "A note is required."}, status=400)
        note = InternalNote.objects.create(ticket=ticket, author=request.user, body=body)
        audit(request.user, "internal_note_added", ticket)
        return JsonResponse({"id": note.pk, "body": note.body}, status=201)
    return JsonResponse({"notes": [{"id": note.pk, "author": note.author.get_full_name() or note.author.username, "body": note.body, "created_at": note.created_at.isoformat()} for note in ticket.internal_notes.select_related("author")]})


@staff_required
@require_POST
def ticket_presence(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    TicketPresence.objects.update_or_create(ticket=ticket, user=request.user, defaults={"last_seen": timezone.now()})
    cutoff = timezone.now() - timedelta(minutes=2)
    viewers = ticket.active_viewers.filter(last_seen__gte=cutoff).exclude(user=request.user).select_related("user")
    return JsonResponse({"viewers": [view.user.get_full_name() or view.user.username for view in viewers]})


@staff_required
@require_http_methods(["GET", "POST"])
def saved_views(request):
    if request.method == "POST":
        payload = json.loads(request.body or "{}")
        view = SavedView.objects.create(owner=request.user, name=payload.get("name", "Saved view")[:80], filters=payload.get("filters", {}))
        return JsonResponse({"id": view.pk, "name": view.name}, status=201)
    return JsonResponse({"views": list(request.user.saved_ticket_views.values("id", "name", "filters"))})


@staff_required
def canned_replies(request):
    replies = CannedReply.objects.filter(shared=True) | CannedReply.objects.filter(owner=request.user)
    return JsonResponse({"replies": list(replies.distinct().values("id", "title", "body"))})


@login_required
@require_http_methods(["GET", "POST"])
def notifications(request):
    if request.method == "POST":
        request.user.support_notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    items = request.user.support_notifications.all()[:40]
    return JsonResponse({"notifications": [{"id": item.pk, "title": item.title, "body": item.body, "url": item.url, "unread": item.read_at is None} for item in items]})


@login_required
@require_POST
def reopen_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id, created_by=request.user)
    if ticket.status not in {Ticket.Status.RESOLVED, Ticket.Status.CLOSED}:
        return JsonResponse({"error": "Only completed tickets can be reopened."}, status=400)
    ticket.status, ticket.reopened_at, ticket.resolved_at = Ticket.Status.OPEN, timezone.now(), None
    ticket.save(update_fields=["status", "reopened_at", "resolved_at", "updated_at"])
    audit(request.user, "ticket_reopened", ticket)
    return JsonResponse({"reopened": True})


@login_required
@require_POST
def rate_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id, created_by=request.user, status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED])
    payload = json.loads(request.body or "{}")
    score = int(payload.get("score", 0))
    if score not in range(1, 6):
        return JsonResponse({"error": "Choose a rating from 1 to 5."}, status=400)
    rating, _ = SatisfactionRating.objects.update_or_create(ticket=ticket, defaults={"customer": request.user, "score": score, "comment": payload.get("comment", "")})
    return JsonResponse({"score": rating.score})


@login_required
def knowledge_search(request):
    query = request.GET.get("q", "").strip()
    articles = KnowledgeArticle.objects.filter(published=True)
    if query:
        articles = articles.filter(models.Q(title__icontains=query) | models.Q(summary__icontains=query) | models.Q(keywords__icontains=query))
    return JsonResponse({"articles": list(articles.values("title", "slug", "summary")[:8])})


@login_required
@require_POST
def ticket_attachment(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    if not request.user.is_staff and ticket.created_by_id != request.user.pk:
        return JsonResponse({"error": "You do not have access to this ticket."}, status=403)
    upload = request.FILES.get("file")
    if not upload or upload.size > 10 * 1024 * 1024:
        return JsonResponse({"error": "Choose a file no larger than 10 MB."}, status=400)
    attachment = TicketAttachment.objects.create(ticket=ticket, uploaded_by=request.user, file=upload, original_name=upload.name)
    audit(request.user, "attachment_uploaded", ticket, name=upload.name)
    return JsonResponse({"id": attachment.pk, "name": attachment.original_name, "url": attachment.file.url}, status=201)


@login_required
@require_http_methods(["GET", "POST"])
def preferences(request):
    preference, _ = UserPreferences.objects.get_or_create(user=request.user)
    if request.method == "POST":
        payload = json.loads(request.body or "{}")
        for field in ("email_new_message", "email_assignment", "browser_notifications", "available"):
            if field in payload:
                setattr(preference, field, bool(payload[field]))
        preference.save()
    fields = ("email_new_message", "email_assignment", "browser_notifications", "available", "two_factor_enabled", "email_verified")
    return JsonResponse({field: getattr(preference, field) for field in fields})


@staff_required
def audit_log(request):
    events = AuditEvent.objects.select_related("actor")[:100]
    target_id = request.GET.get("target_id")
    if target_id:
        events = events.filter(target_id=target_id)
    return JsonResponse({"events": [{"action": event.action, "actor": event.actor.username if event.actor else "System", "target": f"{event.target_type} {event.target_id}", "detail": event.detail, "created_at": event.created_at.isoformat()} for event in events]})


def valid_totp(secret, code):
    try:
        key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
        for offset in (-1, 0, 1):
            counter = int(time.time() // 30) + offset
            digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
            index = digest[-1] & 15
            value = (struct.unpack(">I", digest[index:index + 4])[0] & 0x7fffffff) % 1000000
            if hmac.compare_digest(f"{value:06d}", str(code).zfill(6)):
                return True
    except (ValueError, TypeError):
        pass
    return False


@staff_required
@require_http_methods(["GET", "POST", "DELETE"])
def two_factor_settings(request):
    preference, _ = UserPreferences.objects.get_or_create(user=request.user)
    if request.method == "DELETE":
        preference.two_factor_enabled, preference.totp_secret = False, ""
        preference.save(update_fields=["two_factor_enabled", "totp_secret"])
    elif request.method == "POST":
        payload = json.loads(request.body or "{}")
        if not preference.totp_secret or not valid_totp(preference.totp_secret, payload.get("code")):
            return JsonResponse({"error": "That verification code is invalid."}, status=400)
        preference.two_factor_enabled = True
        preference.save(update_fields=["two_factor_enabled"])
    elif not preference.totp_secret:
        preference.totp_secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        preference.save(update_fields=["totp_secret"])
    issuer = PlatformSettings.load().site_name
    return JsonResponse({"enabled": preference.two_factor_enabled, "secret": preference.totp_secret, "uri": f"otpauth://totp/{issuer}:{request.user.username}?secret={preference.totp_secret}&issuer={issuer}"})


@user_passes_test(lambda user: user.is_active and user.is_superuser, login_url="login")
@require_POST
def revoke_user_sessions(request, user_id):
    revoked = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if str(session.get_decoded().get("_auth_user_id")) == str(user_id):
            session.delete()
            revoked += 1
    AuditEvent.objects.create(actor=request.user, action="sessions_revoked", target_type="User", target_id=str(user_id), detail={"count": revoked})
    return JsonResponse({"revoked": revoked})


@login_required
@require_POST
def send_verification(request):
    if not request.user.email:
        return JsonResponse({"error": "Add an email address first."}, status=400)
    token = signing.dumps({"user": request.user.pk, "email": request.user.email}, salt="supportdesk-email")
    link = request.build_absolute_uri(f"/tickets/verify-email/{token}/")
    send_mail("Verify your SupportDesk email", f"Verify your email address:\n\n{link}", None, [request.user.email])
    return JsonResponse({"sent": True})


def verify_email(request, token):
    try:
        payload = signing.loads(token, salt="supportdesk-email", max_age=86400)
        user = User.objects.get(pk=payload["user"], email=payload["email"])
        preference, _ = UserPreferences.objects.get_or_create(user=user)
        preference.email_verified = True
        preference.save(update_fields=["email_verified"])
    except (signing.BadSignature, User.DoesNotExist, KeyError):
        return JsonResponse({"error": "This verification link is invalid or expired."}, status=400)
    return redirect("login")


@staff_required
@require_POST
def ticket_workflow(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    payload = json.loads(request.body or "{}")
    ticket.category = payload.get("category", ticket.category).strip()[:80]
    ticket.escalation_level = min(max(int(payload.get("escalation_level", ticket.escalation_level)), 0), 3)
    ticket.save(update_fields=["category", "escalation_level", "updated_at"])
    if "tags" in payload:
        tags = [TicketTag.objects.get_or_create(name=name.strip()[:50])[0] for name in payload["tags"] if name.strip()]
        ticket.tags.set(tags)
    audit(request.user, "workflow_updated", ticket, category=ticket.category, escalation=ticket.escalation_level)
    return JsonResponse({"category": ticket.category, "escalation_level": ticket.escalation_level, "tags": list(ticket.tags.values_list("name", flat=True))})


@staff_required
@require_POST
def merge_ticket(request, ticket_id):
    source = get_object_or_404(Ticket, pk=ticket_id)
    target = get_object_or_404(Ticket, pk=json.loads(request.body or "{}").get("target_id"))
    if source == target:
        return JsonResponse({"error": "Choose a different destination ticket."}, status=400)
    source.messages.update(ticket=target)
    source.merged_into, source.status, source.resolved_at = target, Ticket.Status.CLOSED, timezone.now()
    source.save(update_fields=["merged_into", "status", "resolved_at", "updated_at"])
    audit(request.user, "ticket_merged", source, target=target.pk)
    return JsonResponse({"merged": True, "target_id": target.pk})


@staff_required
def agent_workload(request):
    agents = User.objects.filter(is_active=True, is_staff=True).annotate(
        active_tickets=Count("assigned_tickets", filter=~Q(assigned_tickets__status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]))
    ).order_by("active_tickets", "first_name", "username")
    return JsonResponse({"agents": [{"id": agent.pk, "name": agent.get_full_name() or agent.username, "active_tickets": agent.active_tickets, "available": getattr(getattr(agent, "support_preferences", None), "available", True)} for agent in agents]})
