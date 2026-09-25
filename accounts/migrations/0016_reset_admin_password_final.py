from django.db import migrations
import os


def reset_admin_password(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    password = os.environ.get("ADMIN_RESET_PASSWORD")

    if not password:
        print("ADMIN_RESET_PASSWORD is missing")
        return

    try:
        user = User.objects.get(username="Nana")

        user.set_password(password)
        user.is_active = True
        user.can_login = True
        user.save(update_fields=["password", "is_active", "can_login"])

        print("Nana password reset successfully")
        print("Password verification:", user.check_password(password))

    except User.DoesNotExist:
        print("Nana was not found")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_check_admin_login"),
    ]

    operations = [
        migrations.RunPython(reset_admin_password),
    ]
