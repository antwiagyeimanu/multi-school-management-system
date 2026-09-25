from django.db import migrations


def fix_admin_login(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    try:
        user = User.objects.get(username="Nana")
        user.is_active = True
        user.can_login = True
        user.save(update_fields=["is_active", "can_login"])
    except User.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_reset_admin_password_again"),
    ]

    operations = [
        migrations.RunPython(fix_admin_login),
    ]
