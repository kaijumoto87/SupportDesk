import json

from django.contrib.auth.models import User
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from .models import AuditEvent, DirectMessage, InternalNote, Notification, SatisfactionRating, Ticket, TicketMessage


class SupportDeskApiTests(TestCase):
    def setUp(self):
        self.agent = User.objects.create_user("agent", password="test-password", is_staff=True)
        self.other = User.objects.create_user("other", password="test-password")
        self.ticket = Ticket.objects.create(title="Help", description="Details", created_by=self.other)
        self.client.force_login(self.agent)

    def test_messages_are_attached_to_author_and_ordered(self):
        url = reverse("ticket_messages", args=[self.ticket.pk])
        self.client.post(url, json.dumps({"body": "First"}), content_type="application/json")
        self.client.post(url, json.dumps({"body": "Second"}), content_type="application/json")
        payload = self.client.get(url).json()
        self.assertEqual([item["body"] for item in payload["messages"]], ["First", "Second"])
        self.assertEqual(payload["messages"][0]["author_id"], self.agent.pk)

    def test_staff_can_delete_ticket_chat_but_not_individual_messages(self):
        TicketMessage.objects.create(ticket=self.ticket, author=self.agent, body="Remove chat")
        response = self.client.delete(reverse("ticket_chat_delete", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TicketMessage.objects.filter(ticket=self.ticket).exists())

    def test_single_and_bulk_status_updates(self):
        self.client.post(reverse("ticket_status", args=[self.ticket.pk]), json.dumps({"status": "IN_PROGRESS"}), content_type="application/json")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.IN_PROGRESS)
        self.client.post(reverse("ticket_bulk_status"), json.dumps({"ids": [self.ticket.pk], "status": "RESOLVED"}), content_type="application/json")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.RESOLVED)

    def test_internal_notes_are_staff_only_and_audited(self):
        response = self.client.post(reverse("ticket_notes", args=[self.ticket.pk]), json.dumps({"body": "Private context"}), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(InternalNote.objects.filter(ticket=self.ticket, body="Private context").exists())
        self.assertTrue(AuditEvent.objects.filter(action="internal_note_added", target_id=str(self.ticket.pk)).exists())

    def test_direct_messages_are_user_linked_ordered_read_and_chat_deletable(self):
        thread = reverse("direct_message_thread", args=[self.other.pk])
        first = self.client.post(thread, json.dumps({"body": "Hello"}), content_type="application/json").json()["message"]
        self.client.post(thread, json.dumps({"body": "Follow up"}), content_type="application/json")
        payload = self.client.get(thread).json()
        self.assertEqual([message["body"] for message in payload["messages"]], ["Hello", "Follow up"])
        self.assertEqual(payload["messages"][0]["sender_id"], self.agent.pk)
        response = self.client.delete(reverse("direct_chat_delete", args=[self.other.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DirectMessage.objects.filter(Q(sender=self.agent, recipient=self.other) | Q(sender=self.other, recipient=self.agent)).exists())

    def test_user_lookup_requires_a_search_query(self):
        self.assertEqual(self.client.get(reverse("direct_message_users")).json()["users"], [])
        result = self.client.get(reverse("direct_message_users"), {"q": "oth"}).json()["users"]
        self.assertEqual([user["id"] for user in result], [self.other.pk])

    def test_active_direct_chats_remain_visible_and_can_be_archived(self):
        self.client.post(reverse("direct_message_thread", args=[self.other.pk]), json.dumps({"body": "Active"}), content_type="application/json")
        self.assertEqual([user["id"] for user in self.client.get(reverse("direct_message_users")).json()["users"]], [self.other.pk])
        self.client.post(reverse("direct_chat_archive", args=[self.other.pk]))
        self.assertEqual(self.client.get(reverse("direct_message_users")).json()["users"], [])
        archived = self.client.get(reverse("direct_message_users"), {"archived": "1"}).json()["users"]
        self.assertEqual([user["id"] for user in archived], [self.other.pk])


class RoleSeparationTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user("customer", password="test-password")
        self.other = User.objects.create_user("another", password="test-password")
        self.admin = User.objects.create_superuser("root-admin", password="test-password")
        self.ticket = Ticket.objects.create(title="Private", description="Customer only", created_by=self.other)

    def test_customer_gets_portal_but_not_staff_console_or_other_conversations(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("customer_portal")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ticket_list")).status_code, 302)
        self.assertEqual(self.client.get(reverse("ticket_messages", args=[self.ticket.pk])).status_code, 403)
        self.assertEqual(self.client.delete(reverse("ticket_chat_delete", args=[self.ticket.pk])).status_code, 302)
        self.assertEqual(self.client.post(reverse("ticket_notes", args=[self.ticket.pk]), "{}", content_type="application/json").status_code, 302)

    def test_customer_can_reopen_and_rate_own_resolved_ticket(self):
        own = Ticket.objects.create(title="Done", description="Resolved", created_by=self.customer, status=Ticket.Status.RESOLVED)
        self.client.force_login(self.customer)
        rating = self.client.post(reverse("rate_ticket", args=[own.pk]), json.dumps({"score": 5}), content_type="application/json")
        self.assertEqual(rating.status_code, 200)
        self.assertTrue(SatisfactionRating.objects.filter(ticket=own, score=5).exists())
        self.assertEqual(self.client.post(reverse("reopen_ticket", args=[own.pk])).status_code, 200)
        own.refresh_from_db()
        self.assertEqual(own.status, Ticket.Status.OPEN)

    def test_platform_administration_is_superuser_only(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("platform_admin")).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("platform_admin")).status_code, 200)

    def test_superuser_is_redirected_from_customer_portal_to_staff_console(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("customer_portal"))
        self.assertRedirects(response, reverse("ticket_list"))
