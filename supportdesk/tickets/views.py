import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from django.core.mail import send_mail
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import AuditEvent, DirectConversation, DirectMessage, Notification, PlatformSettings, Ticket, TicketMessage


staff_required = user_passes_test(lambda user: user.is_active and user.is_staff, login_url="login")
superuser_required = user_passes_test(lambda user: user.is_active and user.is_superuser, login_url="login")


def _home_for(user):
    return "ticket_list" if user.is_staff else "customer_portal"


def _audit(user, action, target, **detail):
    AuditEvent.objects.create(actor=user, action=action, target_type=target.__class__.__name__, target_id=str(target.pk), detail=detail)


def _notify(user, title, body, url, preference_field="email_new_message"):
    Notification.objects.create(user=user, title=title, body=body, url=url)
    async_to_sync(get_channel_layer().group_send)(f"user_{user.pk}", {"type": "support.event", "payload": {"type": "notification", "title": title, "body": body, "url": url}})
    preference = getattr(user, "support_preferences", None)
    if user.email and (preference is None or getattr(preference, preference_field, True)):
        send_mail(title, f"{body}\n\nOpen SupportDesk: {url}", None, [user.email], fail_silently=True)


@staff_required
def ticket_list(request):
    tickets = Ticket.objects.select_related("created_by", "assigned_to").annotate(message_count=Count("messages")).order_by("-updated_at")
    return render(request, "tickets/ticket_list.html", {
        "tickets": tickets,
        "open_count": tickets.filter(status=Ticket.Status.OPEN).count(),
        "waiting_count": tickets.filter(status=Ticket.Status.IN_PROGRESS).count(),
        "resolved_count": tickets.filter(status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]).count(),
        "my_count": tickets.filter(Q(assigned_to=request.user) | Q(created_by=request.user)).count(),
        "agents": User.objects.filter(is_active=True).order_by("first_name", "username"),
        "customers": User.objects.filter(created_tickets__isnull=False).distinct()[:12],
    })


@login_required
@require_POST
def ticket_create(request):
    title, description = request.POST.get("title", "").strip(), request.POST.get("description", "").strip()
    if not title or not description:
        messages.error(request, "A title and description are required.")
        return redirect("ticket_list")
    priority = request.POST.get("priority", Ticket.Priority.MEDIUM)
    sla_hours = {Ticket.Priority.URGENT: 2, Ticket.Priority.HIGH: 8, Ticket.Priority.MEDIUM: 24, Ticket.Priority.LOW: 72}
    ticket = Ticket.objects.create(title=title, description=description,
        priority=priority, category=request.POST.get("category", "").strip(), due_at=timezone.now() + timedelta(hours=sla_hours.get(priority, 24)), created_by=request.user,
        assigned_to=User.objects.filter(pk=request.POST.get("assigned_to"), is_staff=True).first() if request.user.is_staff else None)
    TicketMessage.objects.create(ticket=ticket, author=request.user, body=description)
    _audit(request.user, "ticket_created", ticket, priority=priority)
    if ticket.assigned_to and ticket.assigned_to != request.user:
        _notify(ticket.assigned_to, f"Assigned: {ticket.title}", f"SD-{ticket.pk:04d} was assigned to you.", "/tickets/#my-tickets", "email_assignment")
    messages.success(request, "Ticket created successfully.")
    return redirect(_home_for(request.user))


@login_required
@require_POST
def account_update(request):
    request.user.first_name = request.POST.get("first_name", "").strip()
    request.user.last_name = request.POST.get("last_name", "").strip()
    request.user.email = request.POST.get("email", "").strip()
    request.user.save(update_fields=["first_name", "last_name", "email"])
    messages.success(request, "Profile updated.")
    return redirect(_home_for(request.user))


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username", ""), password=request.POST.get("password", ""))
        if user:
            preference = getattr(user, "support_preferences", None)
            if user.is_staff and preference and preference.two_factor_enabled:
                request.session["pending_2fa_user"] = user.pk
                return redirect("two_factor_verify")
            login(request, user)
            return redirect(_home_for(user))
        messages.error(request, "The username or password was incorrect.")
    return render(request, "tickets/auth.html", {"mode": "login"})


def two_factor_verify(request):
    user = User.objects.filter(pk=request.session.get("pending_2fa_user"), is_active=True).first()
    if not user:
        return redirect("login")
    if request.method == "POST":
        from .enhancements import valid_totp
        preference = getattr(user, "support_preferences", None)
        if preference and valid_totp(preference.totp_secret, request.POST.get("code")):
            login(request, user)
            request.session.pop("pending_2fa_user", None)
            request.session["two_factor_verified"] = True
            return redirect(_home_for(user))
        messages.error(request, "That authentication code was invalid.")
    return render(request, "tickets/two_factor_verify.html")


def signup_view(request):
    platform = PlatformSettings.load()
    if not platform.allow_customer_signup:
        messages.error(request, "Customer registration is currently closed.")
        return redirect("login")
    if request.user.is_authenticated:
        return redirect(_home_for(request.user))
    if request.method == "POST":
        username, password = request.POST.get("username", "").strip(), request.POST.get("password", "")
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "That username is already in use.")
        elif len(password) < 8:
            messages.error(request, "Use a password with at least 8 characters.")
        else:
            user = User.objects.create_user(username=username, email=request.POST.get("email", "").strip(),
                password=password, first_name=request.POST.get("first_name", "").strip(), last_name=request.POST.get("last_name", "").strip())
            login(request, user)
            return redirect("customer_portal")
    return render(request, "tickets/auth.html", {"mode": "signup"})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def _message_payload(message, request_user):
    author_name = message.author.get_full_name() or message.author.username
    return {
        "id": message.pk,
        "body": message.body,
        "author": author_name,
        "author_id": message.author_id,
        "initials": "".join(part[0] for part in author_name.split())[:2].upper(),
        "created_at": message.created_at.strftime("%b %-d, %Y · %-I:%M %p"),
        "is_current_user": message.author_id == request_user.pk,
    }


@login_required
@require_http_methods(["GET", "POST"])
def ticket_messages(request, ticket_id):
    ticket = get_object_or_404(Ticket.objects.select_related("created_by", "assigned_to"), pk=ticket_id)
    if not request.user.is_staff and ticket.created_by_id != request.user.pk:
        return JsonResponse({"error": "You do not have access to this conversation."}, status=403)
    if request.method == "POST":
        try:
            body = json.loads(request.body or "{}").get("body", "").strip()
        except json.JSONDecodeError:
            body = ""
        if not body:
            return JsonResponse({"error": "A message body is required."}, status=400)
        message = TicketMessage.objects.create(ticket=ticket, author=request.user, body=body)
        if request.user.is_staff and ticket.first_response_at is None:
            ticket.first_response_at = timezone.now()
            ticket.save(update_fields=["first_response_at"])
        if ticket.chat_archived:
            ticket.chat_archived = False
            ticket.save(update_fields=["chat_archived"])
        recipient = ticket.created_by if request.user.is_staff else ticket.assigned_to
        if recipient and recipient != request.user:
            _notify(recipient, f"New reply: {ticket.title}", body[:160], f"/tickets/{'portal/' if not recipient.is_staff else '#inbox'}")
        _audit(request.user, "ticket_message_sent", ticket)
        return JsonResponse({"message": _message_payload(message, request.user)}, status=201)
    return JsonResponse({
        "ticket": {"id": ticket.pk, "status": ticket.status, "status_label": ticket.get_status_display(), "created_by_id": ticket.created_by_id, "assigned_to_id": ticket.assigned_to_id},
        "messages": [_message_payload(message, request.user) for message in ticket.messages.select_related("author")],
    })


@staff_required
@require_http_methods(["DELETE"])
def ticket_chat_delete(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    deleted, _ = ticket.messages.all().delete()
    return JsonResponse({"deleted": True, "messages_deleted": deleted})


@staff_required
@require_POST
def ticket_chat_archive(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    ticket.chat_archived = not ticket.chat_archived
    ticket.save(update_fields=["chat_archived"])
    return JsonResponse({"archived": ticket.chat_archived})


@staff_required
@require_POST
def ticket_status(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    try:
        status = json.loads(request.body or "{}").get("status")
    except json.JSONDecodeError:
        status = None
    valid_statuses = {value for value, _ in Ticket.Status.choices}
    if status not in valid_statuses:
        return JsonResponse({"error": "Invalid ticket status."}, status=400)
    previous = ticket.status
    ticket.status = status
    ticket.resolved_at = timezone.now() if status in {Ticket.Status.RESOLVED, Ticket.Status.CLOSED} else None
    ticket.save(update_fields=["status", "resolved_at", "updated_at"])
    _audit(request.user, "ticket_status_changed", ticket, previous=previous, status=status)
    return JsonResponse({"id": ticket.pk, "status": ticket.status, "status_label": ticket.get_status_display()})


@staff_required
@require_POST
def ticket_bulk_status(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}
    ids, status = payload.get("ids", []), payload.get("status")
    valid_statuses = {value for value, _ in Ticket.Status.choices}
    if status not in valid_statuses or not isinstance(ids, list):
        return JsonResponse({"error": "Invalid bulk update."}, status=400)
    updated = Ticket.objects.filter(pk__in=ids).update(status=status)
    return JsonResponse({"updated": updated, "status": status})


@staff_required
@require_POST
def ticket_assign(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    try:
        assignee_id = json.loads(request.body or "{}").get("assigned_to")
    except json.JSONDecodeError:
        assignee_id = None
    assignee = User.objects.filter(pk=assignee_id, is_active=True, is_staff=True).first() if assignee_id else None
    if assignee_id and not assignee:
        return JsonResponse({"error": "That assignee is unavailable."}, status=400)
    ticket.assigned_to = assignee
    ticket.save(update_fields=["assigned_to", "updated_at"])
    _audit(request.user, "ticket_assigned", ticket, assignee=assignee.pk if assignee else None)
    if assignee and assignee != request.user:
        _notify(assignee, f"Assigned: {ticket.title}", f"{request.user.username} assigned this ticket to you.", "/tickets/#my-tickets", "email_assignment")
    return JsonResponse({
        "id": ticket.pk,
        "assigned_to": assignee.pk if assignee else None,
        "assignee_label": (assignee.get_full_name() or assignee.username) if assignee else "Unassigned",
    })


@login_required
def customer_portal(request):
    # A staff or superuser may arrive here from a saved customer-portal URL
    # (especially on another device). Keep privileged accounts in the staff
    # console regardless of which authenticated URL they open.
    if request.user.is_staff:
        return redirect("ticket_list")
    tickets = Ticket.objects.filter(created_by=request.user).select_related("assigned_to").order_by("-updated_at")
    return render(request, "tickets/customer_portal.html", {
        "tickets": tickets,
        "active_tickets": tickets.exclude(status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]),
        "resolved_tickets": tickets.filter(status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]),
        "platform": PlatformSettings.load(),
    })


@login_required
def platform_access(request):
    return JsonResponse({"allowed": request.user.is_superuser})


@superuser_required
def platform_admin(request):
    platform = PlatformSettings.load()
    if request.method == "POST":
        platform.site_name = request.POST.get("site_name", platform.site_name).strip()
        platform.support_email = request.POST.get("support_email", platform.support_email).strip()
        platform.allow_customer_signup = request.POST.get("allow_customer_signup") == "on"
        platform.save()
        messages.success(request, "Platform settings updated.")
        return redirect("platform_admin")
    return render(request, "tickets/platform_admin.html", {"platform": platform, "users": User.objects.order_by("-is_superuser", "-is_staff", "username")})


@superuser_required
@require_POST
def platform_user_create(request):
    username, password = request.POST.get("username", "").strip(), request.POST.get("password", "")
    if User.objects.filter(username__iexact=username).exists() or len(password) < 8:
        messages.error(request, "Use a unique username and a password of at least 8 characters.")
    else:
        role = request.POST.get("role", "customer")
        User.objects.create_user(username=username, email=request.POST.get("email", "").strip(), password=password,
            first_name=request.POST.get("first_name", "").strip(), last_name=request.POST.get("last_name", "").strip(),
            is_staff=role in {"staff", "admin"}, is_superuser=role == "admin")
        messages.success(request, f"Account {username} created.")
    return redirect("platform_admin")


@superuser_required
@require_POST
def platform_user_update(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user == request.user and request.POST.get("is_active") != "on":
        messages.error(request, "You cannot deactivate your current administrator account.")
        return redirect("platform_admin")
    role = request.POST.get("role", "customer")
    user.is_staff = role in {"staff", "admin"}
    user.is_superuser = role == "admin"
    user.is_active = request.POST.get("is_active") == "on"
    user.email = request.POST.get("email", user.email).strip()
    user.save(update_fields=["is_staff", "is_superuser", "is_active", "email"])
    messages.success(request, f"Account {user.username} updated.")
    return redirect("platform_admin")


def _direct_message_payload(message, request_user):
    sender_name = message.sender.get_full_name() or message.sender.username
    return {
        "id": message.pk,
        "body": message.body,
        "sender": sender_name,
        "sender_id": message.sender_id,
        "is_current_user": message.sender_id == request_user.pk,
        "created_at": message.created_at.strftime("%b %-d, %Y · %-I:%M %p"),
        "is_read": bool(message.read_at),
    }


@login_required
def direct_message_users(request):
    query = request.GET.get("q", "").strip()
    archived = request.GET.get("archived") == "1"
    users = User.objects.filter(is_active=True).exclude(pk=request.user.pk)
    if not request.user.is_staff:
        users = users.filter(is_staff=True)
    if len(query) >= 2:
        users = users.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) |
            Q(last_name__icontains=query) | Q(email__icontains=query)
        )[:20]
    else:
        conversations = DirectConversation.objects.filter(
            Q(participant_low=request.user) | Q(participant_high=request.user), archived=archived
        )
        participant_ids = set(conversations.values_list("participant_low_id", flat=True)) | set(conversations.values_list("participant_high_id", flat=True))
        participant_ids.discard(request.user.pk)
        users = users.filter(pk__in=participant_ids)
    result = []
    for user in users:
        name = user.get_full_name() or user.username
        last = DirectMessage.objects.filter(
            Q(sender=request.user, recipient=user) | Q(sender=user, recipient=request.user)
        ).order_by("-created_at").first()
        result.append({
            "id": user.pk,
            "name": name,
            "username": user.username,
            "email": user.email,
            "initials": "".join(part[0] for part in name.split())[:2].upper(),
            "role": "Administrator" if user.is_superuser else ("Staff" if user.is_staff else "Customer"),
            "unread": DirectMessage.objects.filter(sender=user, recipient=request.user, read_at__isnull=True).count(),
            "last_message": last.body[:80] if last else "Start a conversation",
        })
    return JsonResponse({"users": result})


@login_required
@require_http_methods(["GET", "POST"])
def direct_message_thread(request, user_id):
    other = get_object_or_404(User, pk=user_id, is_active=True)
    if other == request.user or (not request.user.is_staff and not other.is_staff):
        return JsonResponse({"error": "This conversation is unavailable."}, status=403)
    if request.method == "POST":
        try:
            body = json.loads(request.body or "{}").get("body", "").strip()
        except json.JSONDecodeError:
            body = ""
        if not body:
            return JsonResponse({"error": "A message body is required."}, status=400)
        message = DirectMessage.objects.create(sender=request.user, recipient=other, body=body)
        conversation = DirectConversation.for_users(request.user, other)
        if conversation.archived:
            conversation.archived = False
            conversation.save(update_fields=["archived", "updated_at"])
        return JsonResponse({"message": _direct_message_payload(message, request.user)}, status=201)
    DirectConversation.for_users(request.user, other)
    thread = DirectMessage.objects.filter(
        Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user)
    ).select_related("sender", "recipient")
    thread.filter(sender=other, recipient=request.user, read_at__isnull=True).update(read_at=timezone.now())
    other_name = other.get_full_name() or other.username
    return JsonResponse({
        "user": {"id": other.pk, "name": other_name, "email": other.email, "initials": "".join(part[0] for part in other_name.split())[:2].upper()},
        "messages": [_direct_message_payload(message, request.user) for message in thread],
    })


@staff_required
@require_http_methods(["DELETE"])
def direct_chat_delete(request, user_id):
    other = get_object_or_404(User, pk=user_id)
    deleted, _ = DirectMessage.objects.filter(
        Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user)
    ).delete()
    DirectConversation.for_users(request.user, other).delete()
    return JsonResponse({"deleted": True, "messages_deleted": deleted})


@staff_required
@require_POST
def direct_chat_archive(request, user_id):
    other = get_object_or_404(User, pk=user_id, is_active=True)
    conversation = DirectConversation.for_users(request.user, other)
    conversation.archived = not conversation.archived
    conversation.save(update_fields=["archived", "updated_at"])
    return JsonResponse({"archived": conversation.archived})
