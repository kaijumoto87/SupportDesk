from django.db import migrations


def seed_initial_messages(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    TicketMessage = apps.get_model("tickets", "TicketMessage")
    for ticket in Ticket.objects.all().iterator():
        if not TicketMessage.objects.filter(ticket_id=ticket.pk).exists():
            TicketMessage.objects.create(
                ticket_id=ticket.pk,
                author_id=ticket.created_by_id,
                body=ticket.description,
                created_at=ticket.created_at,
            )


def remove_seeded_messages(apps, schema_editor):
    TicketMessage = apps.get_model("tickets", "TicketMessage")
    TicketMessage.objects.filter(pk__in=TicketMessage.objects.values("ticket_id").annotate()).delete()


class Migration(migrations.Migration):
    dependencies = [("tickets", "0002_ticketmessage")]
    operations = [migrations.RunPython(seed_initial_messages, migrations.RunPython.noop)]
