from django.db import migrations
import os


def reset_admin_password(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    password = os.environ.get("ADMIN_RESET_PASSWORD")

    if not password:
        return

    try:
        user = User.objects.get(username="Nana")
        user.set_password(password)
        user.save(update_fields=["password"])
    except User.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_reset_admin_password"),
    ]

    operations = [
        migrations.RunPython(reset_admin_password),
    ]
