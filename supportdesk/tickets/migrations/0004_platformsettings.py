from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tickets", "0003_seed_initial_messages")]
    operations = [
        migrations.CreateModel(
            name="PlatformSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("site_name", models.CharField(default="SupportDesk", max_length=80)),
                ("support_email", models.EmailField(default="support@example.com", max_length=254)),
                ("allow_customer_signup", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name_plural": "platform settings"},
        ),
    ]
