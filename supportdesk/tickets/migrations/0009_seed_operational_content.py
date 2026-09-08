from django.db import migrations


def seed(apps, schema_editor):
    Article = apps.get_model("tickets", "KnowledgeArticle")
    Reply = apps.get_model("tickets", "CannedReply")
    articles = [
        ("Login and portal access", "login-and-portal-access", "Steps for password, session, redirect, and account-access problems.", "login password access authentication portal redirect"),
        ("Account and permission problems", "account-permissions", "Diagnose inactive accounts, incorrect roles, invitations, and workspace permissions.", "account role permission staff customer invite"),
        ("Files, exports, and integrations", "files-exports-integrations", "Common checks for uploads, downloads, APIs, webhooks, and data exports.", "file upload export api webhook integration"),
    ]
    for title, slug, summary, keywords in articles:
        Article.objects.get_or_create(slug=slug, defaults={"title": title, "summary": summary, "keywords": keywords, "content": summary})
    for title, body in [("Request more details", "Thanks for contacting support. Could you share the steps you took, what you expected, and what happened instead?"), ("Issue resolved", "We’ve applied a fix and marked this request resolved. Please reply or reopen it if the problem returns.")]:
        Reply.objects.get_or_create(title=title, defaults={"body": body, "shared": True})


class Migration(migrations.Migration):
    dependencies = [("tickets", "0008_alter_ticket_options_userpreferences_totp_secret")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
