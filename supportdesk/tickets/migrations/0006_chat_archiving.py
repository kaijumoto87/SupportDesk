import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("tickets", "0005_directmessage")]
    operations = [
        migrations.AddField(model_name="ticket", name="chat_archived", field=models.BooleanField(default=False)),
        migrations.CreateModel(
            name="DirectConversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("archived", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("participant_high", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="direct_conversations_high", to=settings.AUTH_USER_MODEL)),
                ("participant_low", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="direct_conversations_low", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name="directconversation", constraint=models.UniqueConstraint(fields=("participant_low", "participant_high"), name="unique_direct_conversation")),
    ]
