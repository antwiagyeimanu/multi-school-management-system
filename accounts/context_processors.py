from datetime import date
from .utils import get_active_term, is_basic_school
from .models import SchoolSetting
from datetime import date, timedelta
from .models import Term  # adjust if needed
from django.utils import timezone
from .utils import calculate_remaining_school_days
from .models import AcademicCalendar
from .models import Announcement, AnnouncementRead
from django.db.models import Q
from .utils import get_active_week
from accounts.utils import get_weeks_for_term, get_active_week

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
        active_term = Term.objects.get(school=request.user.school, is_active=True)

    except Term.DoesNotExist:
        return {}

    except Term.MultipleObjectsReturned:
        active_term = (
            Term.objects.filter(school=request.user.school, is_active=True)
            .order_by("-start_date")
            .first()
        )

        if not active_term:
            return {}

    if not active_term.start_date or not active_term.end_date:
        return {}

    today = date.today()

    # Use the Admin-defined school calendar
    weeks = get_weeks_for_term(active_term)

    if not weeks:
        return {}

    total_weeks = len(weeks)

    # -----------------------------------------
    # GLOBAL TOP-BAR WEEK
    # -----------------------------------------

    term_key = f"top_week_term_{active_term.id}"

    week_param = request.GET.get("top_week")

    if week_param:
        try:
            active_week = int(week_param)

            # Remember the selected top-bar week
            request.session[term_key] = active_week
            request.session.modified = True

        except (ValueError, TypeError):
            active_week = request.session.get(term_key)

    else:
        # Use the remembered top-bar week for this term
        active_week = request.session.get(term_key)

        # If nothing has been selected yet, use the actual current week
        if active_week is None:
            active_week = get_active_week(
                active_term.start_date, today, active_term.end_date
            )

            request.session[term_key] = active_week
            request.session.modified = True

    # Keep the week inside the current term
    active_week = max(1, min(active_week, total_weeks))

    week_start, week_end = weeks[active_week - 1]

    return {
        "today": today,
        "active_week": active_week,
        "week_start": week_start,
        "week_end": week_end,
        "active_term": active_term,
        "prev_week": max(1, active_week - 1),
        "next_week": min(total_weeks, active_week + 1),
        "top_week_offset": active_week,
        "week": active_week,
    }


def global_context(request):
    return {
        "today_date": timezone.now().date(),
        "current_time": timezone.now(),
        "is_basic_school": is_basic_school(request.user),
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
        elif 'specific_class' in roles or 'specific class' in roles:
                    if user_class:
                        # check new many-to-many
                        if ann.target_classes.filter(id=user_class.id).exists():
                            show = True
                        # fallback old field
                        elif ann.target_class_id and user_class.id == ann.target_class_id:
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
