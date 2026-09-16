import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sms.settings")
django.setup()

from accounts.models import AttendanceSession, AttendanceRecord

print("Fixing sessions...")
fixed_s = 0
sessions = AttendanceSession.objects.filter(school__isnull=True)
for s in sessions:
    if hasattr(s, "school_class") and s.school_class and s.school_class.school:
        s.school = s.school_class.school
        s.save()
        fixed_s = fixed_s + 1
print("Fixed sessions:", fixed_s)

print("Fixing records...")
fixed_r = 0
records = AttendanceRecord.objects.filter(school__isnull=True)
for r in records:
    if r.session and r.session.school:
        r.school = r.session.school
        r.save()
        fixed_r = fixed_r + 1
print("Fixed records:", fixed_r)
print("DONE")
