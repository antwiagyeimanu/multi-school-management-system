from datetime import date, timedelta
from .models import AcademicYear, Term
from .models import AcademicCalendar

STAGE_LABELS = {
    'creche': 'Creche',
    'nursery': 'Nursery',
    'kg': 'KG',
    'lower_primary': 'Lower Primary',
    'upper_primary': 'Upper Primary',
    'jhs': 'JHS',
    'shs': 'SHS',
}

def get_active_term(school):
    """Returns the term where is_active=True for this school"""
    return Term.objects.filter(school=school, is_active=True).first()

def get_academic_year(check_date, school):
    """Get academic year from the term that contains check_date"""
    term = Term.objects.filter(
        school=school,
        start_date__lte=check_date,
        end_date__gte=check_date
    ).first()
    return term.academic_year if term else None

def get_day_status(check_date, school):
    """
    Returns: (status, event_name, term)
    status = 'school_day', 'holiday', 'exam_day', 'revision_day', 'weekend', 'closed'
    """
    # 1. Weekends = no school
    if check_date.weekday() >= 5:  # 5=Sat, 6=Sun
        return 'weekend', 'Weekend', None
    
    # 2. Get the term this date falls in
    term = Term.objects.filter(
        school=school,
        start_date__lte=check_date,
        end_date__gte=check_date
    ).first()
    
    if not term:
        return 'closed', 'No Term Set', None
    
 
    
    event = AcademicCalendar.objects.filter(
        school=school,
        start_date__lte=check_date,
        end_date__gte=check_date,
        affects_timetable=True
    ).first()
    
    if event:
        if event.event_type in ['public_holiday', 'midterm_holiday', 'vacation']:
            return 'holiday', event.name, term
        elif event.event_type in ['midterm_exams', 'endterm_exams']:
            return 'exam_day', event.name, term
        elif event.event_type == 'revision_week':
            return 'revision_day', event.name, term
        else:
            return 'event_day', event.name, term
    
    # 4. If date is inside term dates, it’s a school day
    return 'school_day', None, term

def is_school_day(check_date, school):
    """Quick True/False check"""
    status, _, _ = get_day_status(check_date, school)
    return status == 'school_day'

def get_next_school_day(start_date, school):
    """Find next real school day, skipping holidays/weekends"""
    next_day = start_date + timedelta(days=1)
    while not is_school_day(next_day, school):
        next_day += timedelta(days=1)
        # safety break if we go past all terms
        if next_day.year > start_date.year + 2:
            break
    return next_day

def get_next_term_begins(school):
    """Uses the active term’s next_term_begins field"""
    active_term = get_active_term(school)
    return active_term.next_term_begins if active_term else None

def calculate_school_days(start, end, school):
    days = 0
    current = start

    while current <= end:
        if is_school_day(current, school):
            days += 1
        current += timedelta(days=1)

    return days



def get_week_info(school, week_number=None):
    """
    Returns academic week information for the active term.
    Week 1 starts on the actual term opening day.
    Every following week runs Monday-Friday.
    """

    term = get_active_term(school)

    if not term:
        return None

    start_date = term.start_date
    end_date = term.end_date

    def get_total_weeks():
        week = 1
        current_start = start_date

        while current_start <= end_date:
            friday = current_start + timedelta(days=(4 - current_start.weekday()))
            current_start = friday + timedelta(days=3)
            week += 1

        return week - 1

    total_weeks = get_total_weeks()

    if week_number is None:

        today = date.today()

        if today < start_date:
            week_number = 1
        else:
            days_diff = (today - start_date).days
            start_weekday = start_date.weekday()
            week_number = ((days_diff + start_weekday) // 7) + 1

    week_number = max(1, min(week_number, total_weeks))

    current_start = start_date

    for _ in range(week_number - 1):
        friday = current_start + timedelta(days=(4 - current_start.weekday()))
        current_start = friday + timedelta(days=3)

    week_start = current_start
    week_end = week_start + timedelta(days=(4 - week_start.weekday()))

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
    """
    Reusable Academic Year + Term filter
    Used across student, parent, teacher and admin pages.
    """

    years = AcademicYear.objects.all(
    ).order_by('-start_date')


    selected_year = request.GET.get('year')
    selected_term = request.GET.get('term')


    selected_term_obj = None


    if selected_year and selected_term:

        selected_term_obj = Term.objects.filter(
            school=school,
            academic_year_id=selected_year,
            term_number=selected_term
        ).first()


    return {
        'years': years,
        'selected_year': selected_year,
        'selected_term': selected_term,
        'selected_term_obj': selected_term_obj,
    }