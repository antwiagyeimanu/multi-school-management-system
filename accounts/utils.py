from datetime import date, timedelta
from .models import AcademicYear, Term
from .models import AcademicCalendar
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP
from .models import GradingScale


STAGE_LABELS = {
    "creche": "Creche",
    "nursery": "Nursery",
    "kg": "KG",
    "lower_primary": "Lower Primary",
    "upper_primary": "Upper Primary",
    "jhs": "JHS",
    "shs": "SHS",
}


# ==========================
# NEW CORE - ADMIN CONTROLS, SAT/SUN ESCAPED
# ==========================
def get_weeks_for_term(term):
    start = term.start_date
    end = term.end_date
    if not start or not end or start > end:
        return []

    # If admin sets Sat/Sun, move to Monday
    if start.weekday() >= 5:
        start = start + timedelta(days=(7 - start.weekday()))

    weeks = []
    # WEEK 1: admin start -> Friday (partial if Thu start = Thu-Fri)
    first_friday = start + timedelta(days=(4 - start.weekday()))
    if first_friday > end:
        first_friday = end
    weeks.append((start, first_friday))

    next_monday = first_friday + timedelta(days=3)
    while next_monday <= end:
        friday = next_monday + timedelta(days=4)
        if friday > end:
            friday = end  # Last week Mon-Thu if term ends Thu
        weeks.append((next_monday, friday))
        next_monday += timedelta(days=7)
    return weeks


def get_active_week(start_date, today, term_end=None):
    if not start_date or not term_end:
        return 1
    if today < start_date:
        return 1

    class FakeTerm:
        def __init__(self, s, e):
            self.start_date = s
            self.end_date = e

    weeks = get_weeks_for_term(FakeTerm(start_date, term_end))
    if not weeks:
        return 1
    if today > term_end:
        return len(weeks)

    for i, (ws, we) in enumerate(weeks, start=1):
        # include Saturday Sunday in same week
        if i < len(weeks):
            next_mon = weeks[i][0] if i < len(weeks) else None
            sunday = (
                next_mon - timedelta(days=1) if next_mon else we + timedelta(days=2)
            )
        else:
            sunday = we + timedelta(days=2)
        if ws <= today <= sunday:
            return i
    return len(weeks)


def get_active_term(school):

    today = timezone.now().date()
    term = Term.objects.filter(
        school=school, start_date__lte=today, end_date__gte=today
    ).first()
    if term:
        return term
    return (
        Term.objects.filter(school=school, is_active=True)
        .order_by("-start_date")
        .first()
    )


def get_academic_year(check_date, school):
    term = Term.objects.filter(
        school=school, start_date__lte=check_date, end_date__gte=check_date
    ).first()
    return term.academic_year if term else None


def get_day_status(check_date, school):
    if check_date.weekday() >= 5:
        return "weekend", "Weekend", None
    term = Term.objects.filter(
        school=school, start_date__lte=check_date, end_date__gte=check_date
    ).first()
    if not term:
        return "closed", "No Term Set", None
    event = AcademicCalendar.objects.filter(
        school=school,
        start_date__lte=check_date,
        end_date__gte=check_date,
        affects_timetable=True,
    ).first()
    if event:
        if event.event_type in ["public_holiday", "midterm_holiday", "vacation"]:
            return "holiday", event.name, term
        elif event.event_type in ["midterm_exams", "endterm_exams"]:
            return "exam_day", event.name, term
        elif event.event_type == "revision_week":
            return "revision_day", event.name, term
        else:
            return "event_day", event.name, term
    return "school_day", None, term


def is_school_day(check_date, school):
    status, _, _ = get_day_status(check_date, school)
    return status == "school_day"


def get_next_school_day(start_date, school):
    next_day = start_date + timedelta(days=1)
    while not is_school_day(next_day, school):
        next_day += timedelta(days=1)
        if next_day.year > start_date.year + 2:
            break
    return next_day


def get_next_term_begins(school):
    active_term = get_active_term(school)
    return active_term.next_term_begins if active_term else None


def get_week_info(school, week_number=None):
    term = get_active_term(school)
    if not term:
        return None

    weeks = get_weeks_for_term(term)
    if not weeks:
        return None

    total_weeks = len(weeks)

    if week_number is None:
        today = date.today()
        week_number = get_active_week(term.start_date, today, term.end_date)

    week_number = max(1, min(week_number, total_weeks))
    week_start, week_end = weeks[week_number - 1]

    return {
        "term": term,
        "week": week_number,
        "week_start": week_start,
        "week_end": week_end,
        "previous_week": max(1, week_number - 1),
        "next_week": min(total_weeks, week_number + 1),
        "total_weeks": total_weeks,
    }


def get_term_year_filter(request, school):
    years = AcademicYear.objects.filter(school=school).order_by("-start_date")
    selected_year = request.GET.get("year")
    selected_term = request.GET.get("term")
    selected_term_obj = None
    if selected_year and selected_term:
        selected_term_obj = Term.objects.filter(
            school=school, academic_year_id=selected_year, term_number=selected_term
        ).first()
    return {
        "years": years,
        "selected_year": selected_year,
        "selected_term": selected_term,
        "selected_term_obj": selected_term_obj,
    }


def calculate_remaining_school_days(today, end, school):
    days = 0
    current = today + timedelta(days=1)
    while current <= end:
        is_weekend = current.weekday() in [5, 6]
        is_holiday = AcademicCalendar.objects.filter(
            school=school,
            start_date__lte=current,
            end_date__gte=current,
            affects_timetable=True,
        ).exists()
        if not is_weekend and not is_holiday:
            days += 1
        current += timedelta(days=1)
    return days


def calculate_school_days(start, end, school):
    days = 0
    current = start
    while current <= end:
        is_weekend = current.weekday() in [5, 6]
        is_holiday = AcademicCalendar.objects.filter(
            school=school,
            start_date__lte=current,
            end_date__gte=current,
            affects_timetable=True,
        ).exists()
        if not is_weekend and not is_holiday:
            days += 1
        current += timedelta(days=1)
    return days


def calculate_total_school_days(start_date, end_date, school):
    from django.utils import timezone

    today = timezone.now().date()
    real_end = end_date if end_date < today else today
    return calculate_school_days(start_date, real_end, school)


def is_basic_school(user):
    return user.is_authenticated and user.school and user.school.edition == "basic"


def get_grading_scale(school, term, score):

    rounded_score = Decimal(str(score)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    grading = GradingScale.objects.filter(
        school=school,
        term=term,
        min_score__lte=rounded_score,
        max_score__gte=rounded_score,
    ).first()

    if not grading:
        grading = GradingScale.objects.filter(
            school=school,
            term__isnull=True,
            min_score__lte=rounded_score,
            max_score__gte=rounded_score,
        ).first()

    return grading
