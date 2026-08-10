from datetime import date
from .utils import get_active_term  
from .models import SchoolSetting
from datetime import date, timedelta
from .models import Term  # adjust if needed
from django.utils import timezone
from .utils import calculate_remaining_school_days
from .models import AcademicCalendar
from .models import Announcement, AnnouncementRead
from django.db.models import Q
from .utils import get_active_week

def active_term(request):
    if request.user.is_authenticated and hasattr(request.user, 'school'):
        active_term = get_active_term(request.user.school)

        remaining_days = 0
        total_open_days = 0

        if active_term and active_term.start_date and active_term.end_date:
            today = date.today()

            # Calculate total open days for the whole term
            total_open_days = calculate_total_school_days(
                active_term.start_date,
                active_term.end_date,
                request.user.school
            )
            

            # Calculate remaining days from today
            if today <= active_term.end_date:
                remaining_days = calculate_remaining_school_days(
                    today,
                    active_term.end_date,
                    request.user.school
                )

        return {
            'active_term': active_term,
            'remaining_days': remaining_days,
            'total_open_days': total_open_days
        }

    return {
        'active_term': None,
        'remaining_days': 0,
        'total_open_days': 0
    }


def attendance_mode(request):
    setting = SchoolSetting.objects.first()
    return {
        "attendance_mode": setting.attendance_mode if setting else "subject"
    }


def week_info(request):

    if not request.user.is_authenticated:
        return {}

    if not hasattr(request.user, "school") or not request.user.school:
        return {}

    try:
        active_term = Term.objects.get(
            school=request.user.school,
            is_active=True
        )
    except Term.DoesNotExist:
        return {}

    except Term.MultipleObjectsReturned:
            active_term = (
                Term.objects
                .filter(school=request.user.school, is_active=True)
                .order_by("-start_date")
                .first()
            )
            if not active_term: # <-- ADD THIS 2 LINES
                return {}

    today = date.today()
    if not active_term.start_date: # <-- AND THIS
            return {}
    school_start = active_term.start_date



    def get_week_range(start_date, week_number):
        current_start = start_date
        for _ in range(week_number - 1):
            friday = current_start + timedelta(days=(4 - current_start.weekday()))
            current_start = friday + timedelta(days=3)
        week_start = current_start
        week_end = week_start + timedelta(days=(4 - week_start.weekday()))
        return week_start, week_end

    def get_total_weeks(start_date, end_date):
        week = 1
        current_start = start_date
        while current_start <= end_date:
            friday = current_start + timedelta(days=(4 - current_start.weekday()))
            current_start = friday + timedelta(days=3)
            week += 1
        return week - 1

    total_weeks = get_total_weeks(school_start, active_term.end_date)

    # READ BOTH week AND top_week - so top bar works!
    week_param =  request.GET.get("top_week")
    if week_param:
        try:
            active_week = int(week_param)
        except:
            active_week = get_active_week(school_start, today)
    else:
        active_week = get_active_week(school_start, today)

    if active_week < 1:
        active_week = 1
    if active_week > total_weeks:
        active_week = total_weeks

    prev_week = active_week - 1
    next_week = active_week + 1
    if prev_week < 1:
        prev_week = 1
    if next_week > total_weeks:
        next_week = total_weeks

    week_start, week_end = get_week_range(school_start, active_week)

    return {
        "today": date.today(),
        "active_week": active_week,
        "week_start": week_start,
        "week_end": week_end,
        "active_term": active_term,
        "prev_week": prev_week,
        "next_week": next_week,
        "top_week_offset": active_week,  # for parent page
        "week": active_week,  # compatibility
    }


def global_context(request):
    return {
        'today_date': timezone.now().date(),
        'current_time': timezone.now(),
    }

def calculate_total_school_days(start_date, end_date, school):
    
    today = timezone.now().date()  # TODAY inclusive, not yesterday
    # Use today if term not finished, else term end
    real_end = end_date if end_date < today else today

    days = 0
    current = start_date
    while current <= real_end:
        is_weekend = current.weekday() in [5, 6]
        is_holiday = AcademicCalendar.objects.filter(
            school=school,
            start_date__lte=current,
            end_date__gte=current,
            affects_timetable=True
        ).exists()
        if not is_weekend and not is_holiday:
            days += 1
        current += timedelta(days=1)
    return days


def announcements_processor(request):
    if not request.user.is_authenticated:
        return {
            'unread_announcements': [], 
            'all_announcements': [],
            'high_priority_announcements': [],
            'medium_priority_announcements': [],
            'low_priority_announcements': []
        }
    
    user = request.user
    today = timezone.now().date()
    
    # Base active announcements
    base_qs = Announcement.objects.filter(
        school=user.school,
        is_active=True
    ).filter(
        Q(expiry_date__isnull=True) | Q(expiry_date__gte=today)
    ).exclude(created_by=user).order_by('-created_at')

    # NEW LOGIC: filter in Python because target_roles is a list
    visible_announcements = []
    user_role = getattr(user, 'role', '').lower()
    user_class = getattr(user, 'school_class', None)

    for ann in base_qs:
        roles = ann.target_roles or []
        # Normalize to lower
        roles = [r.lower() for r in roles]

        show = False
        if 'all' in roles:
            show = True
        elif user_role in roles:
            show = True
        elif 'support_staff' in roles and user_role in ['support_staff', 'staff', 'non_teaching', 'kitchen', 'cleaner', 'driver']:
            show = True
        elif 'specific_class' in roles:
            if user_class and ann.target_class_id and user_class.id == ann.target_class_id:
                show = True
        
        # Also handle old plural names just in case
        if not show:
            if user_role == 'student' and 'students' in roles:
                show = True
            if user_role == 'teacher' and 'teachers' in roles:
                show = True
            if user_role == 'parent' and 'parents' in roles:
                show = True

        if show:
            visible_announcements.append(ann)

    # UNREAD = not in acknowledged_by
    all_announcements = visible_announcements
    unread_announcements = [a for a in visible_announcements if user not in a.acknowledged_by.all()]

    high_priority_announcements = []
    medium_priority_announcements = []
    low_priority_announcements = []
    
    for ann in unread_announcements:
        prio = (ann.priority or '').strip().upper()
        if prio == 'HIGH':
            high_priority_announcements.append(ann)
        elif prio == 'MEDIUM':
            medium_priority_announcements.append(ann)
        else:
            low_priority_announcements.append(ann)
    
    return {
        'unread_announcements': unread_announcements,
        'all_announcements': all_announcements[:10],
        'critical_announcements': high_priority_announcements,
        'medium_priority_announcements': medium_priority_announcements,
        'low_priority_announcements': low_priority_announcements,
    }
