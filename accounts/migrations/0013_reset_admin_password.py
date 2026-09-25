from django.db import migrations
import os


def reset_admin_password(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    password = os.environ.get("ADMIN_RESET_PASSWORD")

    if not password:
        print("ADMIN_RESET_PASSWORD is missing")
        return
    print("ADMIN_RESET_PASSWORD is present")

    try:
        user = User.objects.get(username="Nana")
        user.set_password(password)
        user.save(update_fields=["password"])
    except User.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_alter_term_sba_total_gradingscale"),
    ]

    operations = [
        migrations.RunPython(reset_admin_password),
    ]
