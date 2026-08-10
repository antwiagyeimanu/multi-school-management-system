# accounts/views.py
# Django imports
import re
import random 
import secrets
import string
import uuid
import io
import json
from django.core.exceptions import ValidationError
from .utils import get_term_year_filter
from django.http import HttpResponseRedirect
from django.db import models
from accounts.utils import STAGE_LABELS
from .models import StudentRemark
from io import BytesIO
from decimal import Decimal, InvalidOperation
from functools import wraps
from itertools import groupby
from operator import attrgetter
from collections import defaultdict
from accounts.models import ContinuousAssessment
from django.db.models import Value
from django.db.models.functions import Concat
from django.contrib.auth import authenticate
from datetime import date, datetime, timedelta
from django.contrib.auth.views import LoginView
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import Announcement, AnnouncementRead
from .forms import AnnouncementForm
from .forms import SchoolPaymentSettingsForm
from django.db.models import (
    Q, Count, Sum, F, FloatField, Avg, Prefetch, Value,
    ExpressionWrapper, Window
)
from django.contrib.auth.hashers import check_password
from django.views.decorators.http import require_http_methods


from django.db.models.functions import Rank, Coalesce
from django.db.models.functions import Rank
from django.http import HttpResponse, Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db import transaction, IntegrityError
from django.template.loader import render_to_string, get_template
from weasyprint import HTML
from xhtml2pdf import pisa
import openpyxl
from openpyxl.styles import Font, Alignment
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from django import template
register = template.Library()
from .utils import calculate_school_days, calculate_remaining_school_days, get_active_week
import requests
from django.views.decorators.csrf import csrf_exempt

# Local models - import everything once here
from .models import (
    CanteenPayment, PaymentTransaction, PaymentItem, PaymentType, Subject, AttendanceSession, School, SchoolSetting, Notification, NotificationRecipient,
    StudentFee, Term, Timetable, AcademicCalendar, FeePayment, StudentSubjectClass, AcademicYear, Fee, PendingPayment, PendingPaymentItem, FeeAuditLog, TeacherAttendance,
    Expense, STAGE_GROUP_MAP, FeeStructure, SchoolClass, AttendanceRecord, Result, TeacherSubjectClass, ParentStudentLink, StudentTermSummary, TeacherAttendance, Break,
    TermSetting
)
from django.db.models import Case, When, Value, IntegerField, CharField, Min

from .forms import (
    ExpenseForm, TeacherForm, AccountantForm, StudentForm, PendingPaymentForm,
    TeacherEditForm, AddSubjectToClassForm, ResultUploadForm, SchoolSmsSettingsForm,
    TermSettingForm, SchoolClassForm, TermForm, AcademicCalendarForm,
    AccountantEditForm, StudentFeeForm, ResultForm, AssignTeacherForm
)

# External apps
from academics.models import Exam

User = get_user_model()

COMMON_PASSWORDS = {
    "password", "12345678", "123456789", "qwerty",
    "admin123", "password123", "123456", "abc123",
    "school123", "welcome", "letmein"
}

TERM_CHOICES = [
    ('1', 'Term 1'),
    ('2', 'Term 2'),
    ('3', 'Term 3'),
]
import logging
logger = logging.getLogger(__name__)
def is_admin(user):
    return user.role in ['admin', 'school_owner']


def check_clash(school, teacher_id, class_id, weekday, start_time, end_time, exclude_id=None):
    clashes = Timetable.objects.filter(
        school=school,
        weekday=weekday,
        start_time__lt=end_time,
        end_time__gt=start_time
    ).filter(
        Q(teacher_id=teacher_id) | Q(school_class_id=class_id)
    )

    if exclude_id:
        clashes = clashes.exclude(id=exclude_id)

    return clashes.select_related(
        'teacher',
        'subject',
        'school_class'
    )


def redirect_to_dashboard(user):
    """Send user to correct dashboard based on role"""
    if user.role == 'admin':
        return redirect('accounts:admin_dashboard')
    elif user.role == 'teacher':
        return redirect('accounts:teacher-dashboard')
    elif user.role == 'student':
        return redirect('accounts:student_dashboard')
    elif user.role == 'parent':
        return redirect('accounts:parent_dashboard')
    elif user.role == 'accountant':
        return redirect('accounts:accountant_dashboard')
    else:
        return redirect('accounts:home')  # fallback

def calculate_grade(total_score):
    if total_score >= 80: return "A"
    elif total_score >= 70: return "B"
    elif total_score >= 60: return "C"
    elif total_score >= 50: return "D"
    elif total_score >= 40: return "E"
    else: return "F"


def role_required(allowed_roles=[]):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')

            if request.user.role not in allowed_roles:
                if request.user.role == 'admin':
                    return redirect('accounts:admin_dashboard')
                elif request.user.role == 'teacher':
                    return redirect('accounts:teacher_dashboard')
                elif request.user.role == 'student':
                    return redirect('accounts:student_dashboard')
                elif request.user.role == 'parent':
                    return redirect('accounts:parent_dashboard')
                elif request.user.role == 'accountant':
                    return redirect('accountant_dashboard')
                else:
                    return redirect('accounts:login')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

# --------------------------
# ADMIN
# --------------------------
@login_required
def dashboard(request):
    user = request.user
    role = user.role

    if role == "admin":
        return redirect('accounts:admin_dashboard')

    elif role == "teacher":
        return redirect('teacher-dashboard')

    elif role == "student":
        return redirect('student_dashboard')

    elif role == "parent":
        return redirect('parent_dashboard')

    elif role == "accountant":
        return redirect('accountant_dashboard')
    else:

     return redirect('accounts:login')


@login_required
def admin_dashboard(request):

    if request.user.role != "admin":
        return redirect("accounts:dashboard")

    school = request.user.school

    if school:

        total_students = User.objects.filter(
            school=school,
            role="student"
        ).count()

        total_teachers = User.objects.filter(
            school=school,
            role="teacher"
        ).count()

        total_parents = User.objects.filter(
            school=school,
            role="parent"
        ).count()

        total_accountants = User.objects.filter(
            school=school,
            role="accountant"
        ).count()

        total_users = User.objects.filter(
            school=school
        ).count()

        total_classes = SchoolClass.objects.filter(
            school=school
        ).count()

        total_subjects = Subject.objects.filter(
            school=school
        ).count()

        pending_count = Expense.objects.filter(
            school=school,
            verified=False
        ).count()


    else:

        total_students = 0
        total_teachers = 0
        total_parents = 0
        total_accountants = 0
        total_users = 0
        total_classes = 0
        total_subjects = 0
        pending_count = 0


    context = {

        "school": school,

        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_parents": total_parents,
        "total_accountants": total_accountants,

        "total_users": total_users,

        "total_classes": total_classes,
        "total_subjects": total_subjects,

        "pending_count": pending_count,

    }


    return render(
        request,
        "accounts/admin_dashboard.html",
        context
    )


@login_required
def admin_profile(request):
    context = {'admin': request.user}
    return render(request, 'accounts/admin_profile.html', context)


@login_required
def student_dashboard(request):
    if request.user.role!= 'student':
        return redirect_to_dashboard(request.user)

    student = request.user

    current_term = Term.objects.filter(
        school=student.school,
        is_active=True
    ).select_related('academic_year').first()

    if not current_term:
        return render(request, 'accounts/student_dashboard.html', {
            'student': student,
            'student_class': student.school_class,
            'current_term': None,
            'total_fees': 0,
            'balance': 0,
            'average_score': 0,
            'total_subjects': 0,
            'attendance_percent': 0,
            'attendance_percent_text': '0%',
            'today_timetable': [],
            'announcements': [],
            'announcement_count': 0,
            'term_grade': '-',
            'term_remark': 'No results',
        })

    student_results = Result.objects.filter(
        student=student,
        term=current_term,
        academic_year=current_term.academic_year,
        status='published'
    )

    totals = [r.total_score or 0 for r in student_results]
    avg_score = sum(totals) / len(totals) if totals else 0
    total_subjects = student_results.count()

    def get_term_grade(pct):
        if pct >= 80: return "A"
        elif pct >= 70: return "B"
        elif pct >= 60: return "C"
        elif pct >= 50: return "D"
        elif pct >= 40: return "E"
        else: return "F"

    def get_term_remark(pct):
        if pct >= 80: return "Excellent"
        elif pct >= 70: return "Very Good"
        elif pct >= 60: return "Good"
        elif pct >= 50: return "Pass"
        elif pct >= 40: return "Weak"
        else: return "Fail"

    term_grade = get_term_grade(avg_score) if total_subjects > 0 else "-"
    term_remark = get_term_remark(avg_score) if total_subjects > 0 else "No results"

    # Attendance - FINAL RULE: ONLY P and A, 1 P = Whole day Present
    today_date = timezone.now().date()

    spent_days = calculate_school_days(
        current_term.start_date,
        today_date,
        student.school
    )

    total_days = calculate_school_days(
        current_term.start_date,
        current_term.end_date,
        student.school
    )

    attendance_records = AttendanceRecord.objects.filter(
        student=student,
        session__date__gte=current_term.start_date,
        session__date__lte=today_date
    ).values('session__date', 'status')

    daily_statuses = defaultdict(list)
    for record in attendance_records:
        daily_statuses[record['session__date']].append(record['status'])

    present_days = 0
    for statuses in daily_statuses.values():
        if 'P' in statuses:
            present_days += 1

    attendance_percent = round((present_days / spent_days) * 100, 1) if spent_days else 0
    attendance_display = f"{present_days}/{spent_days}"

    fee_record = StudentFee.objects.filter(
        student=student,
        term=current_term,
        academic_year=current_term.academic_year
    ).first()

    today = timezone.now().date()
    weekday_int = today.weekday()
    today_timetable = []
    if weekday_int <= 4:
        today_timetable = Timetable.objects.filter(
            school = student.school,
            school_class=student.school_class,
            weekday=weekday_int
        ).select_related('subject','teacher').order_by('start_time')

    all_announcements = Announcement.objects.filter(
        school=student.school,
        is_active=True
    ).order_by('-created_at')

    filtered = []
    for ann in all_announcements:
        roles = ann.target_roles or []
        if isinstance(roles, str):
            roles = roles.replace("'", "").replace("[", "").replace("]", "").replace(" ", "").split(",")
        if 'all' in roles or 'students' in roles:
            filtered.append(ann)
        elif 'specific_class' in roles and ann.target_class_id == student.school_class_id:
            filtered.append(ann)

    announcement_count = len(filtered)
    announcements = filtered[:5]

    context = {
        'student': student,
        'student_class': student.school_class,
        'current_term': current_term,
        'average_score': round(avg_score, 1),
        'total_subjects': total_subjects,
        'attendance_percent': attendance_percent,
        'attendance_percent_text': f"{attendance_percent}%",
        'total_fees': fee_record.total_amount if fee_record else 0,
        'balance': fee_record.balance() if fee_record else 0,
        'fee_record': fee_record,
        'results': student_results,
        'today_timetable': today_timetable,
        'announcements': announcements,
        'announcement_count': announcement_count,
        'term_grade': term_grade,
        'term_remark': term_remark,
        'present_days': present_days,
        'spent_days': spent_days,
        'total_days': total_days,
        'attendance_display': attendance_display,
    }
    return render(request, 'accounts/student_dashboard.html', context)

# ----------------------------
# SIGNUP VIEW
# ----------------------------
def signup_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = User.objects.create_user(
            username=username,
            password=password
        )

        messages.success(request, "Account created successfully!")
        return redirect('accounts:login')

    return render(request, 'accounts/signup.html')


# ----------------------------
# LOGIN VIEW
# ----------------------------
def login_view(request):
    if request.method == "POST":
        identifier = request.POST.get("username")
        password = request.POST.get("password")
        user = User.objects.filter(
            Q(username=identifier) |
            Q(staff_id=identifier) |
            Q(student_number=identifier)
        ).first()
        if not user:
            messages.error(request, "Invalid login details")
            return render(request, "accounts/login.html")
        if user.role == "student" and not user.can_login:
            messages.error(request, "Your account is not active yet. Contact admin.")
            return render(request, "accounts/login.html")
        authenticated_user = authenticate(
            request,
            username=user.username,
            password=password
        )
        if authenticated_user:
            login(request, authenticated_user)
            if not authenticated_user.is_password_changed:
                return redirect('accounts:change-password')
            if authenticated_user.role == "admin":
                return redirect('accounts:admin_dashboard')
            elif authenticated_user.role == "teacher":
                return redirect('accounts:teacher-dashboard')
            elif authenticated_user.role == "student":
                return redirect('accounts:student_dashboard')
            elif authenticated_user.role == "accountant":
                return redirect('accounts:accountant_dashboard')
            elif authenticated_user.role == "parent":   # <-- added
                return redirect('accounts:parent_dashboard')  # <-- added
            return redirect('accounts:dashboard')  # <-- added accounts:
        messages.error(request, "Invalid login details")
    return render(request, "accounts/login.html")

# --------------------------------
# PASSWORD
# -------------------------------
@login_required
def change_password(request):
    if request.method == "POST":
        password1 = request.POST.get("password1", "").strip()
        password2 = request.POST.get("password2", "").strip()
        
        if password1 != password2:
            messages.error(request, "Passwords do not match.")
            return redirect('accounts:change-password')

        user = request.user

        if user.check_password(password1):
            messages.error(request, "You cannot reuse your current password.")
            return redirect('accounts:change-password')
        
        user.set_password(password1)
        user.is_password_changed = True
        user.save()
        
        update_session_auth_hash(request, user)
        messages.success(request, "Password changed successfully.")
        
        if user.role == 'teacher':
            return redirect('accounts:teacher-dashboard')
        elif user.role == 'student':
            return redirect('accounts:student_dashboard')
        elif user.role == 'accountant':
            return redirect('accounts:accountant_dashboard')
        elif user.role == 'parent':
            return redirect('accounts:parent_dashboard')
        else:
            return redirect('accounts:admin_dashboard')

    return render(request, "accounts/change-password.html")


# --------------------------
# SET STONG PASSWORD
# ------------------------
def is_strong_password(password):
    if not password:
        return False, "Password cannot be empty."

    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    
    if password.lower() in COMMON_PASSWORDS:
        return False, "This password is too common."

    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter."

    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least one number."

    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must contain at least one symbol (e.g @, #, !)."

    return True, "Password is strong."


# ---------------------------
# LOGOUT VIEW
# ----------------------------
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('accounts:login')


@login_required
def toggle_student_login(request, student_id):

    if request.user.role != "admin":
        return redirect('accounts:dashboard')

    if request.method != "POST":
        return redirect('accounts:manage-students')

    student = get_object_or_404(
        User,
        id=student_id,
        role='student',
        school=request.user.school
    )

    student.can_login = not student.can_login
    student.save()

    if student.can_login:
        messages.success(
            request,
            f"{student.get_full_name()} can now log in."
        )
    else:
        messages.success(
            request,
            f"{student.get_full_name()} has been blocked from logging in."
        )

    return redirect(request.META.get('HTTP_REFERER', 'accounts:manage-students'))

# ----------------------------
# DASHBOARD
# ----------------------------
@login_required
def dashboard(request):
    school = request.user.school  
    
    if request.user.role == 'admin':
        context = {
            'total_students': User.objects.filter(role='student', school=school).count(),  
            'total_teachers': User.objects.filter(role='teacher', school=school).count(),
            'total_parents': User.objects.filter(role='parent', school=school).count(),
            'total_users': User.objects.filter(school=school).count(),
        }
        return render(request, 'accounts/admin_dashboard.html', context)
    elif request.user.role == 'teacher':
        return render(request, 'accounts/teacher_dashboard.html')
    elif request.user.role == 'student':
        return render(request, 'accounts/student_dashboard.html')
    elif request.user.role == 'parent':
        return render(request, 'accounts/parent_dashboard.html')
    return render(request, 'accounts/dashboard_base.html')

# ----------------------------
# ADMIN VIEWS
# --------------------------------


@login_required
def manage_students(request, class_id=None):
    credentials = request.session.pop("credentials", None)
    if request.user.role == 'admin':
        allowed_class_ids = SchoolClass.objects.filter(
            school=request.user.school
        ).values_list('id', flat=True)
    else:  
        allowed_class_ids = TeacherSubjectClass.objects.filter(
            school_class__school=request.user.school,
            teacher=request.user
        ).values_list('school_class_id', flat=True).distinct()
    
    if class_id and int(class_id) not in allowed_class_ids:
        raise Http404("You are not assigned to teach in this class")
    
    classes = SchoolClass.objects.filter(id__in=allowed_class_ids, school = request.user.school)
    all_students = User.objects.filter(
        is_active=True,
        role='student',
        school=request.user.school,
        student__school_class_id__in=allowed_class_ids
    ).select_related('student')
    
    search_query = request.GET.get('q', '')
    class_filter = request.GET.get('class', '')
    subject_filter = request.GET.get('subject', '')
    
    if class_id:
        all_students = all_students.filter(school_class_id=class_id)
        current_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
    else:
        current_class = None
    
    if search_query:
        all_students = all_students.filter(
            Q(first_name__icontains=search_query) | 
            Q(last_name__icontains=search_query) |
            Q(student_number__icontains=search_query)
        )
    
    if class_filter:
        all_students = all_students.filter(school_class_id=class_filter)
    
    if subject_filter:
        all_students = all_students.filter(
            studentsubjectclass__subject_id=subject_filter
        ).distinct()
    
    all_students = all_students.order_by('last_name')
    paginator = Paginator(all_students, 4)
    page_number = request.GET.get('page', 1)
    students = paginator.get_page(page_number)
    
    if request.user.role == 'admin':
        subjects = Subject.objects.filter(school=request.user.school)
    else:
        subject_ids = TeacherSubjectClass.objects.filter(
            teacher=request.user
        ).values_list('subject_id', flat=True).distinct()
        subjects = Subject.objects.filter(id__in=subject_ids)
    
    context = {
        'students': students,
        'classes': classes,
        'subjects': subjects,
        'all_subjects': subjects,
        'search_query': search_query,
        'class_filter': class_filter,
        'subject_filter': subject_filter,
        'current_class': current_class,
        'credentials': credentials

    }
    return render(request, "accounts/manage_students.html", context)

@login_required
def manage_students_grid(request, class_id=None):
    credentials = request.session.pop("credentials", None)
    if request.user.role == 'admin':
        allowed_class_ids = SchoolClass.objects.filter(
            school=request.user.school
        ).values_list('id', flat=True)
    else:  
        allowed_class_ids = TeacherSubjectClass.objects.filter(
            school_class__school=request.user.school,
            teacher=request.user
        ).values_list('school_class_id', flat=True).distinct()
    
    if class_id and int(class_id) not in allowed_class_ids:
        raise Http404("You are not assigned to teach in this class")
    
    classes = SchoolClass.objects.filter(id__in=allowed_class_ids, school = request.user.school)
    all_students = User.objects.filter(
        is_active=True,
        role='student',
        school=request.user.school,
        student__school_class_id__in=allowed_class_ids
    ).select_related('student')
    
    search_query = request.GET.get('q', '')
    class_filter = request.GET.get('class', '')
    subject_filter = request.GET.get('subject', '')
    
    if class_id:
        all_students = all_students.filter(school_class_id=class_id)
        current_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
    else:
        current_class = None
    
    if search_query:
        all_students = all_students.filter(
            Q(first_name__icontains=search_query) | 
            Q(last_name__icontains=search_query) |
            Q(student_number__icontains=search_query)
        )
    
    if class_filter:
        all_students = all_students.filter(school_class_id=class_filter)
    
    if subject_filter:
        all_students = all_students.filter(
            studentsubjectclass__subject_id=subject_filter
        ).distinct()
    
    all_students = all_students.order_by('last_name')
    paginator = Paginator(all_students, 4)
    page_number = request.GET.get('page', 1)
    students = paginator.get_page(page_number)
    
    if request.user.role == 'admin':
        subjects = Subject.objects.filter(school=request.user.school)
    else:
        subject_ids = TeacherSubjectClass.objects.filter(
            teacher=request.user
        ).values_list('subject_id', flat=True).distinct()
        subjects = Subject.objects.filter(id__in=subject_ids)
    
    context = {
        'students': students,
        'classes': classes,
        'subjects': subjects,
        'all_subjects': subjects,
        'search_query': search_query,
        'class_filter': class_filter,
        'subject_filter': subject_filter,
        'current_class': current_class,
        'credentials': credentials

    }
    return render(request, "accounts/manage_students.html", context) 


@login_required
def assign_student_subject(request, student_id):

    school = request.user.school

    student = get_object_or_404(
        User,
        id=student_id,
        role='student',
        school=school
    )

    if request.method == 'POST':

        subject_ids = request.POST.getlist('subjects')
        class_ids = request.POST.getlist('classes')

        # Remove old assignments
        StudentSubjectClass.objects.filter(
            student=student
        ).delete()

        subjects = Subject.objects.filter(
            id__in=subject_ids,
            school=school
        )

        classes = SchoolClass.objects.filter(
            id__in=class_ids,
            school=school
        )

        for subject in subjects:
            for school_class in classes:
                StudentSubjectClass.objects.create(
                    student=student,
                    subject=subject,
                    school_class=school_class
                )

        messages.success(
            request,
            f"Subjects assigned to {student.username} successfully!"
        )

        return redirect('accounts:manage-students')


    subjects = Subject.objects.filter(
        school=school
    )

    classes = SchoolClass.objects.filter(
        school=school
    )

    assignments = StudentSubjectClass.objects.filter(
        student=student
    )

    return render(
        request,
        'accounts/assign_student_subject.html',
        {
            'student': student,
            'subjects': subjects,
            'classes': classes,
            'assignments': assignments
        }
    )


@login_required
def assigned_teachers_list(request):
    school = request.user.school

    classes = SchoolClass.objects.filter(
        school=school
    ).prefetch_related(
        Prefetch(
            'teachersubjectclass_set',
            queryset=TeacherSubjectClass.objects.select_related(
                'subject',
                'teacher'
            ).filter(
                teacher__school=school
            )
        )
    ).filter(
        teachersubjectclass__isnull=False
    ).distinct().order_by('name')

    return render(request, 'accounts/assigned_teachers_list.html', {
        'classes': classes
    })


@login_required
def add_student(request):
    credentials = None  

    if request.user.role == 'teacher':
        if not request.user.school.can_teachers_manage_students:
            messages.error(request, "Your school admin has disabled teacher access to add students.")
            return redirect('accounts:teacher_dashboard')

    elif request.user.role not in ['admin', 'teacher']:
        return redirect('accounts:student_dashboard')

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect('accounts:dashboard')

    if request.method == "POST":
        form = StudentForm(request.POST, request.FILES, request=request)

        if form.is_valid():
            student = form.save(commit=False)

            student.role = 'student'
            student.school = request.user.school
            student.is_password_changed = False

            # username
            base_username = f"{student.first_name.lower()}.{student.last_name.lower()}"
            username = base_username
            counter = 1

            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            student.username = username

            # student number
            school_name = request.user.school.name

            words = [w for w in school_name.split() if w.upper() not in ['THE', 'OF', 'AND']]
            school_initials = ''.join([w[0].upper() for w in words if w[0].isalpha()])

            year = str(datetime.now().year)[-2:]

            prefix = f"{school_initials}/STU/{year}/"

            while True:
                random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
                student_number = f"{prefix}{random_part}"

                if not User.objects.filter(student_number=student_number).exists():
                    break

            student.student_number = student_number

            # password
            password = ''.join(secrets.choice(
                'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*'
            ) for _ in range(10))

            student.set_password(password)

            student.can_login = student.school_class.allows_student_login

            student.save()

            request.session["credentials"] = {
                "title": "Student Login Credentials",
                "username": student.username,
                "student_number": student.student_number,
                "password": password
            }

            # auto assign subjects
            if student.school_class:
                class_subjects = student.school_class.subjects.all()
                StudentSubjectClass.objects.bulk_create([
                    StudentSubjectClass(
                        student=student,
                        subject=sub,
                        school_class=student.school_class
                    )
                    for sub in class_subjects
                ], ignore_conflicts=True)

            messages.success(
                request,
                f"Username: {student.username} | Student ID: {student.student_number} | Password: {password}",
                extra_tags="credentials"
            )

            if student.school_class:
                return redirect(
                    'accounts:view-students-in-class',
                    class_id=student.school_class.id
                )

            return redirect('accounts:teacher_dashboard')

        else:
            messages.error(request, "Please fix the errors below.")

    else:
        form = StudentForm(request=request)

    return render(request, 'accounts/add_student.html', {
        "form": form,
        "credentials": credentials
    })


@login_required
def edit_student_assignment(request, id):
    assignment = get_object_or_404(StudentSubjectClass, id=id)

    classes = SchoolClass.objects.all()
    subjects = Subject.objects.all()

    if request.method == 'POST':
        new_class = request.POST.get('school_class')
        new_subject = request.POST.get('subject')

        # 1. Check if user changed anything
        if (str(assignment.school_class_id) == new_class and
            str(assignment.subject_id) == new_subject):
            return redirect('accounts:view-student', student_id=assignment.student.id)

        # 2. Check duplicate
        exists = StudentSubjectClass.objects.filter(
            student=assignment.student,
            school_class_id=new_class,
            subject_id=new_subject
        ).exclude(id=assignment.id).exists()

        if exists:
            return render(request, 'accounts/edit_student_assignment.html', {
                'assignment': assignment,
                'classes': classes,
                'subjects': subjects,
                'error': 'This student already has this class + subject assigned!'
            })

        # Safe to update
        assignment.school_class_id = new_class
        assignment.subject_id = new_subject
        assignment.save()

        messages.success(request, "Assignment updated successfully.")
        return redirect('accounts:view-student', student_id=assignment.student.id)

    return render(request, 'accounts/edit_student_assignment.html', {
        'assignment': assignment,
        'classes': classes,
        'subjects': subjects,
    })

@login_required
def edit_student(request, student_id):
    student = get_object_or_404(User, id=student_id, role='student', school=request.user.school)
    
    if request.user.role == 'teacher':
        if not request.user.school.can_teachers_manage_students:
            messages.error(request, "Your school admin has disabled teacher access to edit students.")
            return redirect('accounts:teacher_dashboard')
        
        teacher_classes = TeacherSubjectClass.objects.filter(
            teacher=request.user
        ).values_list('school_class', flat=True)
        
        if student.school_class_id not in teacher_classes:
            messages.error(request, "You can only edit students in your assigned classes.")
            return redirect('accounts:view-students-in-class', class_id=student.school_class_id)
            
    elif request.user.role not in ['admin', 'teacher']:
        return redirect('accounts:student_dashboard')
    
    # Get classes for dropdown
    if request.user.role == 'teacher':
        teacher_classes = TeacherSubjectClass.objects.filter(
            teacher=request.user
        ).values_list('school_class', flat=True).distinct()
        classes = SchoolClass.objects.filter(id__in=teacher_classes, school=request.user.school)
    else:
        classes = SchoolClass.objects.filter(school=request.user.school)
    
    if request.method == 'POST':
        # PERSONAL INFO
        student.first_name = request.POST.get('first_name', student.first_name)
        student.middle_name = request.POST.get('middle_name', '') or None
        student.last_name = request.POST.get('last_name', student.last_name)
        
        # CONTACT INFO
        student.email = request.POST.get('email', '') 
        student.phone = request.POST.get('phone', '') or None
        
        # CLASS - school is read-only now
        class_id = request.POST.get('school_class')
        if class_id:
            school_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
            
            if request.user.role == 'teacher':
                if not TeacherSubjectClass.objects.filter(teacher=request.user, school_class=school_class).exists():
                    messages.error(request, "You can only assign students to classes you teach.")
                    return redirect('accounts:edit-student', student_id=student.id)
            
            student.school_class = school_class
        
        # BIO DATA
        student.date_of_birth = request.POST.get('date_of_birth') or None
        student.gender = request.POST.get('gender') or None
        student.nationality = request.POST.get('nationality', '') or None
        student.religion = request.POST.get('religion') or None
        
        # GUARDIAN INFO
        student.parent_guardian_name = request.POST.get('parent_guardian_name', '') or None
        student.parent_guardian_phone = request.POST.get('parent_guardian_phone', '') or None

        # EMERGENCY CONTACT
        student.emergency_contact_name = request.POST.get('emergency_contact_name', '') or None
        student.emergency_contact_phone = request.POST.get('emergency_contact_phone', '') or None
        student.emergency_contact_relationship = request.POST.get('emergency_contact_relationship', '') or None
        
        # OTHER
        student.admission_date = request.POST.get('admission_date') or None

        if 'photo' in request.FILES:
            student.photo = request.FILES['photo']
        
        student.save()
        
        messages.success(request, f"{student.get_full_name()} updated successfully!")
        return redirect('accounts:view-student', student_id=student.id)

    context = {
        'student': student,
        'all_classes': classes,
    }
    return render(request, 'accounts/edit_student.html', context)

@login_required
def view_student(request, student_id):

    student = get_object_or_404(
        User,
        id=student_id,
        role='student',
        school=request.user.school
    )
    # ==========================
    # TERM FILTER (FOR RESULTS, ATTENDANCE, FEES)
    # ==========================

    terms = Term.objects.filter(
        school=request.user.school,
    ).order_by(
        'term_number'
    )

    selected_term_id = request.GET.get('term')

    if selected_term_id:
        selected_term = terms.filter(
            id=selected_term_id
        ).first()
    else:
        selected_term = terms.filter(
            is_active=True
        ).first()
        # ==========================
# ACADEMIC YEAR FILTER
    # ==========================

    academic_years = AcademicYear.objects.filter(
        school=request.user.school
    ).order_by('-name')


    selected_year_id = request.GET.get('year')

    if selected_year_id:
        selected_year = academic_years.filter(
            id=selected_year_id
        ).first()
    else:
        selected_year = academic_years.filter(
            is_active=True
    ).first()

    student_subjects = StudentSubjectClass.objects.filter(
        student=student,
        school_class__school=request.user.school
    ).select_related(
        'subject',
        'school_class'
    )

    enrolled_subject_ids = student_subjects.values_list(
        'subject_id',
        flat=True
    ).distinct()


    available_subjects = Subject.objects.filter(
        school=request.user.school
    ).exclude(
        id__in=enrolled_subject_ids
    ).order_by('name')
    setting = SchoolSetting.objects.filter(
        school=request.user.school
    ).first()

    

    attendance_mode = setting.attendance_mode if setting else "subject"
    if attendance_mode == "class_teacher":

        records = AttendanceRecord.objects.filter(
            student=student
        ).select_related(
            'session'
        ).order_by(
            'session__date'
        )


        attendance_records = {}

        for record in records:

            date = record.session.date

            if date not in attendance_records:
                attendance_records[date] = record.get_status_display()

            if record.status == "P":
                attendance_records[date] = "Present"


        attendance_records = [
            {
                "date": date,
                "day": date.strftime("%A"),
                "status": status
            }
            for date, status in attendance_records.items()
        ]

        attendance_records = Paginator(
            attendance_records,
            10
        ).get_page(
            request.GET.get('attendance_page')
        )

    else:

        attendance_records = AttendanceRecord.objects.filter(
            student=student,
            session__date__gte=selected_term.start_date,
            session__date__lte=selected_term.end_date
        
        ).select_related(
            'session',
            'session__subject'
        ).order_by(
            'session__date'
        )
        attendance_records = Paginator(attendance_records,10).get_page(request.GET.get('attendance_page'))
        # ==========================
    # ATTENDANCE SUMMARY
    # ==========================

    attendance_summary = {
        "total_days": 0,
        "present_days": 0,
        "absent_days": 0,
        "percentage": 0,
    }


    if attendance_mode == "class_teacher":

        all_attendance = {}

        records = AttendanceRecord.objects.filter(
            student=student
        ).select_related(
            'session'
        )

        for record in records:

            date = record.session.date

            if date not in all_attendance:
                all_attendance[date] = record.get_status_display()

            if record.status == "P":
                all_attendance[date] = "Present"


        total_days = len(all_attendance)

        present_days = list(all_attendance.values()).count("Present")

        absent_days = total_days - present_days


    else:

        records = AttendanceRecord.objects.filter(
            student=student,
            session__date__gte=selected_term.start_date,
            session__date__lte=selected_term.end_date
        )

        total_days = records.values(
            'session__date'
        ).distinct().count()

        present_days = records.filter(
            status="P"
        ).values(
            'session__date'
        ).distinct().count()

        absent_days = total_days - present_days



    attendance_summary["total_days"] = total_days
    attendance_summary["present_days"] = present_days
    attendance_summary["absent_days"] = absent_days


    if total_days > 0:
        attendance_summary["percentage"] = round(
            (present_days / total_days) * 100,
            1
        )
    # ==========================
    # FEES INFORMATION - FIXED
    # ==========================
    student_fees = StudentFee.objects.filter(
        student=student
    ).select_related('term')

    if selected_term:
        student_fees = student_fees.filter(
            term=selected_term
        )

    if selected_year:
        student_fees = student_fees.filter(
            academic_year=selected_year.name
        )

    # Get all terms for this student
    fee_terms = [fee.term for fee in student_fees]

    payment_items_all = PaymentItem.objects.filter(
        transaction__student=student,
        transaction__term__in=fee_terms,
        transaction__is_voided=False
    )


    fee_breakdowns = []

    for fee in student_fees:
        breakdown = []

        fee_items = [
            ("School Fees", fee.school_fees),
            ("PTA Dues", fee.pta_dues),
            ("Computer Levy", fee.computer_levy),
            ("Exam Fees", fee.exam_fees),
            ("Feeding Fee", fee.canteen_amount),
            ("Other Fees", fee.other_fees),
            ("Development Fee", fee.development_fee),
            ("Boarding Fee", fee.boarding_fee),
            ("Hostel Fee", fee.hostel_fee),
        ]

        for name, amount in fee_items:
            if amount and amount > 0:
                # FIX 1: filter by THIS term only + case-insensitive
                paid = payment_items_all.filter(
                    transaction__term=fee.term,
                    fee_name__iexact=name  # iexact = Canteen = canteen = CANTEEN
                ).aggregate(total=Sum('amount'))['total'] or 0



                breakdown.append({
                    "name": name,
                    "amount": amount,
                    "paid": paid,
                    "balance": amount - paid
                })

        fee_breakdowns.append({
            "fee": fee,
            "items": breakdown
        })

    payment_history = PaymentTransaction.objects.filter(
        student=student,
        term=selected_term,
        academic_year=selected_year,
        is_voided=False

    ).prefetch_related(
        'items'
    ).order_by(
        '-created_at'
    )

    payment_history = Paginator(
        payment_history,
        10
    ).get_page(
        request.GET.get('payment_page')
    )
    # ==========================
    # RESULTS INFORMATION
    # ==========================

    student_results = Result.objects.filter(
        student=student,
        subject__school=request.user.school,
        term=selected_term,
    ).select_related(
        'subject',
        'term',
        'academic_year'
    ).order_by(
        'subject__name'
    )
    
            

    return render(
        request,
        'accounts/view_student.html',
        {
            'student': student,
            'terms': terms,
            'selected_term': selected_term,
            'academic_years': academic_years,
            'selected_year': selected_year,
            'student_subjects': student_subjects,
            'available_subjects': available_subjects,
            'attendance_records': attendance_records,
            'attendance_mode': attendance_mode,
            'attendance_summary': attendance_summary,
            'student_fees': student_fees,
            'fee_breakdowns': fee_breakdowns,
            'payment_history': payment_history,
            'student_results': student_results,
        }
    )


@login_required
def delete_student(request, student_id):

    student_user = get_object_or_404(
        User,
        id=student_id,
        role='student',
        school=request.user.school
    )

    if request.method == 'POST':

        # deactivate student login
        student_user.is_active = False
        student_user.can_login = False
        student_user.save()

        # remove student subject assignments


        messages.success(
            request,
            f'{student_user.get_full_name()} has been deactivated.'
        )

        return redirect(
            'accounts:view-students-in-class',
            class_id=student_user.school_class.id
        )

    return render(
        request,
        'accounts/delete_student.html',
        {
            'student': student_user
        }
    )


@login_required
def assign_student_class(request, student_id):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')  # fixed typo

    # ✅ add school filter - prevent cross-school
    student = get_object_or_404(User, id=student_id, role='student', school=request.user.school)
    
    # ✅ only show your school classes
    classes = SchoolClass.objects.filter(school=request.user.school)

    if request.method == 'POST':
        class_id = request.POST.get('class_id')

        if class_id:
            selected_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
            student.school_class = selected_class
            student.save()

            messages.success(request, f"Class {selected_class.name} assigned to {student.get_full_name()} successfully")
            return redirect('accounts:manage-students')

    return render(request, 'accounts/assign_student_class.html', {
        'student': student,
        'classes': classes
    })

@login_required
def manage_teachers_grid(request, teacher_id=None):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    credentials = request.session.pop("credentials", None)

    # =====================================
    # POST: ASSIGN SUBJECT + CLASS
    # =====================================
    if request.method == "POST":
        selected_teacher = request.POST.get("teacher_id")
        school_class = request.POST.get("school_class")
        subject = request.POST.get("subject")

        if selected_teacher and school_class and subject:

            teacher = get_object_or_404(
                User,
                id=selected_teacher,
                role="teacher",
                school=request.user.school
            )

            school_class_obj = get_object_or_404(
                SchoolClass,
                id=school_class,
                school=request.user.school
            )

            subject_obj = get_object_or_404(
                Subject,
                id=subject,
                school=request.user.school
            )

            TeacherSubjectClass.objects.get_or_create(
                teacher=teacher,
                school_class=school_class_obj,
                subject=subject_obj
            )

        return redirect('accounts:manage-teachers-grid')

    # =====================================
    # ACTIVE TEACHERS ONLY
    # =====================================
    teachers = User.objects.filter(
        role="teacher",
        is_active=True,
        school=request.user.school
    ).annotate(
        assignment_count=Count("teachersubjectclass", distinct=True),
        class_count=Count("teachersubjectclass__school_class", distinct=True),
        subject_count=Count("teachersubjectclass__subject", distinct=True)
    )

    all_teachers = User.objects.filter(role="teacher", school=request.user.school)

    # =====================================
    # SEARCH
    # =====================================
    search_query = request.GET.get("q","").strip()
    if search_query:
            teachers = teachers.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(username__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(staff_id__icontains=search_query)
            )

    # =====================================
    # CLASS FILTER
    # =====================================
    class_filter = request.GET.get("class","")
    if class_filter:
        teachers = teachers.filter(teachersubjectclass__school_class__id=class_filter)

    # =====================================
    # SUBJECT FILTER
    # =====================================
    subject_filter = request.GET.get("subject","")
    if subject_filter:
        teachers = teachers.filter(teachersubjectclass__subject__id=subject_filter)

    teachers = teachers.distinct().order_by('first_name')

    # =====================================
    # PAGINATION - keep filters
    # =====================================
    paginator = Paginator(teachers, 8) # 4 is too small, use 12
    page_obj = paginator.get_page(request.GET.get("page"))

    # keep querystring for pagination links
    query_params = request.GET.copy()
    if 'page' in query_params:
        query_params.pop('page')
    querystring = query_params.urlencode()

    classes = SchoolClass.objects.filter(school=request.user.school)
    subjects = Subject.objects.filter(school=request.user.school)
    highlight_teacher_id = request.GET.get("highlight")

    context = {
        "teachers": page_obj,
        "all_teachers": all_teachers,
        "classes": classes,
        "subjects": subjects,
        "search_query": search_query,
        "class_filter": class_filter,
        "subject_filter": subject_filter,
        "highlight_teacher_id": highlight_teacher_id,
        "teacher_id": teacher_id,
        "credentials": credentials,
        "querystring": querystring,  # use in template: ?page=2&{{ querystring }}
    }

    return render(request, "accounts/manage_teachers_grid.html", context)


@login_required
def add_teacher(request):
    credentials = None

    if request.user.role != 'admin':
        return redirect('accounts:home')

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect('accounts:home')

    if request.method == "POST":
        form = TeacherForm(request.POST, request.FILES, school=request.user.school)

        if form.is_valid():
            teacher = form.save(commit=False)

            teacher.role = 'teacher'
            teacher.school = request.user.school

            # username
            if not teacher.username:
                base_username = f"{teacher.first_name.lower()}.{teacher.last_name.lower()}"
                username = base_username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1
                teacher.username = username

            # password
            safe_chars = string.ascii_letters + string.digits + "!@#$%*"
            password = ''.join(secrets.choice(safe_chars) for _ in range(10))

            teacher.set_password(password)
            teacher.is_password_changed = False

            # cleanup
            if not teacher.is_class_teacher:
                teacher.class_teacher_of = None

            try:
                teacher.save()
                form.save_m2m()
            except Exception as e:
                messages.error(request, f"Database error: {str(e)}")
                return redirect('accounts:add-teacher')
            request.session["credentials"] = {
                "title": "Teacher Login Credentials",
                "username": teacher.username,
                "staff_id": teacher.staff_id,
                "password": password if password else "Not set"
            }


            messages.success(
                request,
                f"Username: {teacher.username} | Staff ID: {teacher.staff_id} | Password: {password}",
                extra_tags="credentials"
            )


            return redirect(
                f"{reverse('accounts:manage-teachers-grid')}?highlight={teacher.id}"
            )

        else:
            messages.error(request, "Please fix the errors below.")

    else:
        form = TeacherForm(school=request.user.school)

    return render(request, "accounts/add_teacher.html", {
        "form": form,
    })


@login_required
def view_teacher(request, teacher_id):
    teacher = get_object_or_404(
        User,
        id=teacher_id,
        role='teacher',
        school=request.user.school
    )

    teacher_classes = TeacherSubjectClass.objects.filter(
        teacher=teacher
    ).annotate(
        slot_count=Count(
            'school_class__timetables',
            filter=Q(
                school_class__timetables__teacher=teacher,
                school_class__timetables__subject=F('subject')
            )
        )
    ).select_related(
        'school_class',
        'subject'
    ).order_by(
        'school_class__name'
    )


    grouped_data = defaultdict(list)

    for tc in teacher_classes:
        if tc.school_class and tc.subject:
            grouped_data[tc.school_class.name].append({
                'subject_name': tc.subject.name,
                'assignment_id': tc.id,
                'slot_count': tc.slot_count,
            })


    return render(request, 'accounts/view_teacher.html', {
        'teacher': teacher,
        'teacher_classes': teacher_classes,
        'grouped_data': grouped_data,
    })


@login_required
def edit_teacher(request, teacher_id):
    teacher = get_object_or_404(User, id=teacher_id, role='teacher', school=request.user.school)

    if request.method == 'POST':
        form = TeacherEditForm(request.POST, request.FILES, instance=teacher)
        if form.is_valid():
            form.save()
            messages.success(request, "Teacher updated successfully!")
            return redirect('accounts:view-teacher', teacher_id=teacher.id)
        else:
            messages.error(request, "Please fix the errors below")
    else:
        form = TeacherEditForm(instance=teacher)

    return render(request, 'accounts/edit_teacher.html', {
        'form': form,
        'teacher': teacher
    })


@login_required
def delete_teacher(request, teacher_id):
    teacher = get_object_or_404(
        User,
        id=teacher_id,
        role='teacher',
        school=request.user.school
    )

    if request.method == 'POST':
        teacher.is_active = False
        teacher.save()

        messages.success(
            request,
            f"{teacher.username} deactivated successfully!"
        )

        return redirect('accounts:manage-teachers-grid')

    return render(
        request,
        'accounts/delete_teacher.html',
        {'teacher': teacher}
    )

@login_required
def deactivated_teachers(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    teachers = User.objects.filter(
        role='teacher',
        is_active=False,
        school=request.user.school
    )

    return render(
        request,
        "accounts/deactivated_teachers.html",
        {
            "teachers": teachers
        }
    )


@login_required
def reactivate_teacher(request, user_id):

    if request.user.role != 'admin':
        return redirect('accounts:dashboard')


    teacher = get_object_or_404(
        User,
        id=user_id,
        role='teacher',
        school=request.user.school
    )


    if request.method == "POST":

        teacher.is_active = True
        teacher.save()

        messages.success(
            request,
            f"{teacher.get_full_name()} has been reactivated."
        )

    return redirect('accounts:deactivated_teachers')

@login_required
def assign_teacher(request, teacher_id):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    teacher = get_object_or_404(User, id=teacher_id, role='teacher', school=request.user.school)

    if request.method == 'POST':
        form = AssignTeacherForm(request.POST)
        # ✅ filter form choices to only your school
        form.fields['school_class'].queryset = SchoolClass.objects.filter(school=request.user.school)
        form.fields['subject'].queryset = Subject.objects.filter(school=request.user.school)

        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.teacher = teacher
            
            # ✅ add school isolation if your model has school field
            if hasattr(assignment, 'school'):
                assignment.school = request.user.school
            
            # ✅ prevent duplicate
            if not TeacherSubjectClass.objects.filter(
                teacher=teacher,
                school_class=assignment.school_class,
                subject=assignment.subject
            ).exists():
                assignment.save()
                messages.success(request, f"Assigned {assignment.subject.name} - {assignment.school_class.name} to {teacher.get_full_name()}")
            else:
                messages.warning(request, "This assignment already exists!")

            return redirect('accounts:assign-teacher', teacher_id=teacher.id)

    else:
        form = AssignTeacherForm()
        form.fields['school_class'].queryset = SchoolClass.objects.filter(school=request.user.school)
        form.fields['subject'].queryset = Subject.objects.filter(school=request.user.school)

    assignments = TeacherSubjectClass.objects.filter(teacher=teacher, school_class__school=request.user.school).select_related('school_class', 'subject')

    return render(request, 'accounts/assign_teacher.html', {
        'teacher': teacher,
        'form': form,
        'assignments': assignments
    })

@login_required
def manage_assignments(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    teachers = User.objects.filter(role='teacher', school=request.user.school, is_active=True).order_by('first_name')
    classes = SchoolClass.objects.filter(school=request.user.school)
    subjects = Subject.objects.filter(school=request.user.school)

    # get assignments for table
    assignments = TeacherSubjectClass.objects.filter(
        teacher__school=request.user.school
    ).select_related('teacher', 'school_class', 'subject').order_by('teacher__first_name')

    return render(request, 'accounts/manage_assignments.html', {
        'teachers': teachers,
        'classes': classes,
        'subjects': subjects,
        'assignments': assignments,
    })

# -----------------------------

# -----------------------

@login_required
def admin_attendance_dashboard(request):
    today = timezone.now().date()

    total_students = User.objects.count()

    present_today = Attendance.objects.filter(
        date=today,
        status="Present"
    ).count()

    absent_today = Attendance.objects.filter(
        date=today,
        status="Absent"
    ).count()

    attendance_percent = 0
    if total_students > 0:
        attendance_percent = round(
            (present_today / total_students) * 100, 2
        )

    context = {
        "total_students": total_students,
        "present_today": present_today,
        "absent_today": absent_today,
        "attendance_percent": attendance_percent,
    }

    return render(request, "accounts/admin_attendance_dashboard.html", context)


@login_required
def manage_users(request):
    users = User.objects.exclude(role__in=['student', 'teacher'])
    return render(request, 'accounts/manage_users.html', {'users': users})


@login_required
def edit_user(request, user_id):
    user = get_object_or_404(User, id=user_id)
    roles = ['admin', 'teacher', 'student', 'parent']

    if request.method == 'POST':
        user.username = request.POST.get('username', user.username)
        user.email = request.POST.get('email', user.email)
        user.role = request.POST.get('role', user.role)
        user.save()
        messages.success(request, f"{user.username} updated successfully!")
        return redirect('accounts:manage-users')

    return render(request, 'accounts/edit_user.html', {'user': user, 'roles': roles})


@login_required
def delete_user(request, user_id):
    user = get_object_or_404(User, id=user_id)
    user.delete()
    messages.success(request, f"{user.username} deleted successfully!")
    return redirect('accounts:manage-users')


@login_required
def manage_parents(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parents = User.objects.filter(role='parent', is_active=True, school=request.user.school)

    # Search filter
    search_query = request.GET.get('q', '')
    if search_query:
        # Split the search query into words
        name_parts = search_query.strip().split()

        if len(name_parts) == 1:
            # If only one word is typed, search in first or last name
            parents = parents.filter(
                Q(first_name__icontains=name_parts[0]) |
                Q(last_name__icontains=name_parts[0])
            )
        else:
            # If multiple words, assume first word = first name, last word = last name
            parents = parents.filter(
                Q(first_name__icontains=name_parts[0]) &
                Q(last_name__icontains=name_parts[-1])
            )

    # Pagination
    paginator = Paginator(parents, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)



    return render(request, 'accounts/manage_parents.html', {
    'parents': page_obj,
    'search_query': search_query
})


@login_required
def add_parent(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    credentials = None

    if request.method == "POST":
        first_name = request.POST.get("first_name","").strip()
        middle_name = request.POST.get("middle_name","").strip()
        last_name = request.POST.get("last_name","").strip()
        email = request.POST.get("email","").strip()
        phone = request.POST.get("phone","").strip()
        occupation = request.POST.get("occupation","").strip()
        address = request.POST.get("address","").strip()
        relationship = request.POST.get("relationship","").strip()

        base_username = f"{first_name}.{last_name}".lower().replace(" ", "")
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        import secrets, string
        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))

        parent = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            email=email,
            phone=phone,
            role='parent',
            school=request.user.school,
            occupation=occupation,
            address=address,
            relationship_type=relationship,
        )

        if request.FILES.get("photo"):
            parent.photo = request.FILES["photo"]
            parent.save()

        credentials = {"username": username, "password": password}

        # ✅ THIS LINE WAS MISSING - ADD IT
        messages.success(request, f"Parent {parent.get_full_name()} created successfully! Username: {username}")

    parents = User.objects.filter(role='parent', school=request.user.school)

    return render(request, 'accounts/add_parent.html', {
        'parents': parents,
        'credentials': credentials
    })


@login_required
def view_parent(request, parent_id):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parent = get_object_or_404(User, id=parent_id, role='parent', school=request.user.school)

    children = parent.student_links.select_related('student')

    return render(request, 'accounts/view_parent.html', {
        'parent': parent,
        'children': children
    })


@login_required
def edit_parent(request, parent_id):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parent = get_object_or_404(User, id=parent_id, role='parent',     school=request.user.school)

    if request.method == "POST":
        parent.first_name = request.POST.get("first_name", "").strip()
        parent.middle_name = request.POST.get("middle_name", "").strip()
        parent.last_name = request.POST.get("last_name", "").strip()
        parent.email = request.POST.get("email", "").strip()
        parent.phone = request.POST.get("phone", "").strip()
        parent.qualification = request.POST.get("occupation", "").strip()
        parent.parent_guardian_name = request.POST.get("relationship", "").strip()
        parent.address = request.POST.get("address", "").strip()

        # PHOTO (IMPORTANT)
        if "photo" in request.FILES:
            parent.photo = request.FILES["photo"]

        parent.save()
        messages.success(request, "Parent updated successfully!")
        return redirect('accounts:view_parent', parent_id=parent.id)

    return render(request, 'accounts/edit_parent.html', {
        'parent': parent
    })


@login_required
def delete_parent(request, parent_id):

    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parent = get_object_or_404(
        User,
        id=parent_id,
        role='parent',
        school=request.user.school
    )

    if request.method == 'POST':
        parent.is_active = False
        parent.save()

        messages.success(
            request,
            f"{parent.username} deactivated successfully!"
        )

    return redirect('accounts:manage-parents')

@login_required
def deactivated_parents(request):

    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parents = User.objects.filter(
        role='parent',
        is_active=False,
        school=request.user.school
    )

    return render(
        request,
        "accounts/deactivated_parents.html",
        {"parents": parents}
    )

@login_required
def reactivate_parent(request, user_id):

    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parent = get_object_or_404(
        User,
        id=user_id,
        role='parent',
        school=request.user.school
    )

    parent.is_active = True
    parent.save()

    messages.success(
        request,
        f"{parent.get_full_name()} has been reactivated."
    )

    return redirect('accounts:deactivated_parents')

@login_required
def add_result(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        form = ResultForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('accounts:dashboard')
    else:

     form = ResultForm()
    form.fields['student'].queryset = User.objects.filter(role='student')
    return render(request, 'accounts/add_result.html', {'form': form})

@login_required
def assign_subject(request, teacher_id):
    teacher = User.objects.get(id=teacher_id, role='teacher')
    subjects = Subject.objects.all()

    if request.method == 'POST':
        subject_id = request.POST.get('subject_id')
        subject = Subject.objects.get(id=subject_id)
        teacher.subjects.add(subject)  # assuming ManyToManyField
        messages.success(request, f"{subject.name} assigned to {teacher.username}")
        return redirect('accounts:manage-teachers')

    return render(request, 'accounts/assign_subject.html', {
        'teacher': teacher,
        'subjects': subjects
    })

# ----------------------------
# PROFILE VIEWS
# ----------------------------
@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html')


# ----------------------------
# TEACHER VIEWS
# ----------------------------

# ---------------------------
# TEACHER DASHBOARD
# --------------------------
@role_required(['teacher'])
def teacher_dashboard(request):
    
    teacher = request.user
    school = teacher.school

    if not school:
        messages.error(
            request,
            "Your account is not assigned to any school."
        )
        return redirect("accounts:home")


    setting = SchoolSetting.objects.filter(
        school=school
    ).first()
        

    attendance_mode = (
        setting.attendance_mode
        if setting else "subject"
    )
    real_today = timezone.now().date()
    today_attendance = TeacherAttendance.objects.filter(
        teacher=teacher,
        school=school,
        date=real_today
    ).first()


    can_check_in = True
    can_check_out = False


    if today_attendance:

        if today_attendance.time_in:
            can_check_in = False

        if today_attendance.time_in and not today_attendance.time_out:
            can_check_out = True
    
    
    try:
        week_offset = int(request.GET.get('week', '0') or 0)
    except ValueError:
        week_offset = 0
    
    monday = real_today - timedelta(days=real_today.weekday()) + timedelta(weeks=week_offset)
    friday = monday + timedelta(days=4)
    school_days = [monday + timedelta(days=i) for i in range(5)]
    school_days = [monday + timedelta(days=i) for i in range(5)]

    teacher_weekdays = set(
        Timetable.objects.filter(teacher=teacher, weekday__in=range(5))
        .values_list('weekday', flat=True)
    )

    scheduled_dates = [d for d in school_days if d.weekday() in teacher_weekdays]
    total_scheduled_this_week = len(scheduled_dates)

    teacher_sessions = AttendanceSession.objects.filter(teacher=teacher, date__in=scheduled_dates)
    marked_sessions_this_week = teacher_sessions.filter(records__isnull=False).distinct().count()
    last_marked_session = teacher_sessions.filter(records__isnull=False).order_by('-date').first()
    last_marked_day_name = last_marked_session.date.strftime('%A').upper() if last_marked_session else ''
    pending_sessions_this_week = total_scheduled_this_week - marked_sessions_this_week
    
    teacher_weekdays = set(
        Timetable.objects.filter(teacher=teacher, weekday__in=range(5))
        .values_list('weekday', flat=True)
    )
    
    display_date = None
    for weekday in range(4, -1, -1):
        if weekday in teacher_weekdays:
            candidate_date = monday + timedelta(days=weekday)
            if monday <= candidate_date <= friday:
                display_date = candidate_date
                break
    
    if display_date is None:
        display_date = friday
    
    if week_offset == 0 and real_today.weekday() < 5 and real_today.weekday() in teacher_weekdays:
        display_date = real_today
        display_date_label = "Today"
    else:
        display_date_label = display_date.strftime('%A')  # "Thursday", "Friday", etc
    
    def get_day_suffix(day):
        if 11 <= day <= 13: return 'th'
        elif day % 10 == 1: return 'st'
        elif day % 10 == 2: return 'nd'
        elif day % 10 == 3: return 'rd'
        else: return 'th'
    
    date_range = f"{monday.strftime('%b')} {monday.day}{get_day_suffix(monday.day)} - {friday.strftime('%b')} {friday.day}{get_day_suffix(friday.day)}"
    
    if week_offset == 0:
        week_label = "This Week"
    elif week_offset == -1:
        week_label = "Last Week" 
    elif week_offset == 1:
        week_label = "Next Week"
    else:
        week_label = f"{abs(week_offset)} Weeks {'Ago' if week_offset < 0 else 'Ahead'}"
    
    assignments = TeacherSubjectClass.objects.filter(teacher=teacher).select_related('school_class', 'subject')
    teacher_classes = assignments.values_list('school_class', flat=True)
    teacher_subjects = assignments.values_list('subject', flat=True)

    students_count = User.objects.filter(role='student', school_class_id__in=teacher_classes).distinct().count()
    classes_count = assignments.values('school_class').distinct().count()
    subjects_count = assignments.values('subject').distinct().count()
    
    base_results = Result.objects.filter(student__school_class_id__in=teacher_classes, subject_id__in=teacher_subjects)
    draft_count = base_results.filter(status__in=['draft', 'returned']).count()
    submitted_count = base_results.filter(status='submitted').count()
    returned_count = base_results.filter(status='returned').count()
    
    timetable_today = Timetable.objects.filter(teacher=teacher, weekday=display_date.weekday())
    total_classes_today = timetable_today.count()
    
    marked_today = AttendanceSession.objects.filter(
        teacher=teacher, date=display_date, records__isnull=False
    ).distinct().count()
    
    pending_today = total_classes_today - marked_today
    
    week_records = AttendanceRecord.objects.filter(
        session__teacher=teacher, session__date__in=school_days
    )
    total_week = week_records.exclude(status='excuse').count()
    present_week = week_records.filter(status__in=['present', 'late']).count()
    absent_week = week_records.filter(status='absent').count()
    late_week = week_records.filter(status='late').count()
    excused_week = week_records.filter(status='excuse').count()
    present_percent = round((present_week / total_week * 100), 1) if total_week > 0 else 0
    today_attendance = TeacherAttendance.objects.filter(
        school=school,
        teacher=teacher,
        date=real_today
    ).first()

    can_check_in = False
    can_check_out = False

    if not today_attendance:
        can_check_in = True
    elif today_attendance.time_in and not today_attendance.time_out:
        can_check_out = True


    current_hour = timezone.localtime().hour

    if current_hour < 12:
        greeting = "Good Morning"
    elif current_hour < 14:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    today_date = timezone.localdate()

    return render(request, 'accounts/teacher_dashboard.html', {
        'assignments': assignments,
        'students_count': students_count,
        'classes_count': classes_count,
        'subjects_count': subjects_count,
        'draft_count': draft_count,
        'submitted_count': submitted_count,
        'returned_count': returned_count,
        'marked_today': marked_today,
        'total_classes_today': total_classes_today,
        'pending_today': pending_today,
        'present_percent': present_percent,
        'present_week': present_week,
        'absent_week': absent_week,
        'late_week': late_week,
        'excused_week': excused_week,
        'total_week': total_week,
        'marked_sessions_this_week': marked_sessions_this_week,
        'last_marked_day_name': last_marked_day_name,
        'total_scheduled_this_week': total_scheduled_this_week,
        'date_range': date_range,
        'week_label': week_label,
        'week_offset': week_offset,
        'display_date_label': display_date_label,
        'today': real_today,
        'attendance_mode': attendance_mode,
        'is_class_teacher': teacher.is_class_teacher,
        'pending_sessions_this_week': pending_sessions_this_week,
        'school_setting': SchoolSetting.objects.first(),
        'today_attendance': today_attendance,
        'can_check_in': can_check_in,
        'can_check_out': can_check_out,
        "greeting": greeting,
        "today_date": today_date,
                
    })


@role_required(['teacher'])
def teacher_timetable(request):
    teacher = request.user
    today_date = timezone.now().date()
    today_weekday = today_date.weekday()
    
    try:
        week_offset = int(request.GET.get('week', '0') or 0)
    except ValueError:
        week_offset = 0
    
    start_of_week = today_date - timedelta(days=today_date.weekday())
    start_of_week += timedelta(weeks=week_offset)
    
    timetable = Timetable.objects.filter(
        teacher=teacher
    ).select_related('subject', 'school_class').order_by('weekday', 'period')
    
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    timetable_by_day = []
    
    for day_num in range(5):  
        current_date = start_of_week + timedelta(days=day_num)
        
        event = AcademicCalendar.objects.filter(
            start_date__lte=current_date,
            end_date__gte=current_date
        ).first()
        
        entries = [e for e in timetable if e.weekday == day_num]
        
        if event and event.affects_timetable:
            entries = []
        
        timetable_by_day.append({
            'num': day_num,
            'name': days[day_num],
            'date': current_date,
            'entries': entries,
            'event': event,
        })
    
    return render(request, 'accounts/teacher_timetable.html', {
        'timetable_by_day': timetable_by_day,
        'today': today_date,  
        'today_weekday': today_weekday if week_offset == 0 else -1,  
        'week_offset': week_offset,
        'prev_week': week_offset - 1,
        'next_week': week_offset + 1,
        'current_week_start': start_of_week,
    })


# ---------------------------
# TEACHER CLASSES
# --------------------------
@login_required
def teacher_classes(request):
    if request.user.role != "teacher":
        return redirect('accounts:dashboard')
    return render(request, 'accounts/teacher_classes.html')

# -----------------------------------
# TEACHER UPLOAD RESULTS
# ----------------------------------

@login_required
def upload_result(request, class_id, subject_id):
    school_class = get_object_or_404(SchoolClass, id=class_id)
    subject = get_object_or_404(Subject, id=subject_id)
    school = school_class.school
    term = Term.objects.filter(is_active=True, school=school).first()
    if not term:
        messages.error(request, "No active term set. Ask admin to activate a term first.")
        return redirect('accounts:teacher-dashboard')

    entered_results = Result.objects.filter(
        subject=subject,
        term=term,
        academic_year=term.academic_year,
        student__school_class=school_class
    ).select_related('student')

    if request.method == "POST":
        form = ResultUploadForm(request.POST, class_id=class_id)

        if form.is_valid():

            student = form.cleaned_data['student']
            ca_data = request.POST.get('ca_data')
            ContinuousAssessment.objects.filter(
                student=student,
                subject=subject,
                term=term,
                academic_year=term.academic_year
            ).delete()

            import json

            result = form.save(commit=False)

            # --- CALCULATE CA ---
            ca_total = 0
            if ca_data:
                assessments = json.loads(ca_data)
                ca_total = sum(float(i['score']) for i in assessments)

            # --- EXAM SCORE ---
            exam = float(result.exam_score or 0)
            # Skip completely empty result
            if ca_total == 0 and exam == 0:
                messages.error(request, "Please enter CA or Exam score before saving.")
                return redirect(
                    'accounts:teacher-upload-results',
                    class_id=class_id,
                    subject_id=subject_id
                )

            # --- LIMITS FROM TERM ---
            max_ca = term.ca_total
            max_exam = term.exam_total

            # --- VALIDATION ---
            if ca_total > max_ca:
                messages.error(request, f"CA cannot be more than {max_ca}")
                return redirect('accounts:teacher-upload-results', class_id=class_id, subject_id=subject_id)

            if exam > max_exam:
                messages.error(request, f"Exam cannot be more than {max_exam}")
                return redirect('accounts:teacher-upload-results', class_id=class_id, subject_id=subject_id)

            if ca_total > exam:
                messages.error(request, "CA cannot be greater than Exam score")
                return redirect('accounts:teacher-upload-results', class_id=class_id, subject_id=subject_id)

            if ca_total + exam > 100:
                messages.error(request, "Total cannot be more than 100")
                return redirect('accounts:teacher-upload-results', class_id=class_id, subject_id=subject_id)

            # --- CHECK EXISTING ---
            existing = Result.objects.filter(
                student=student,
                subject=subject,
                term=term,
                academic_year=term.academic_year
            ).exists()

            if existing:
                messages.error(request, f"Result already exists for {student.get_full_name()}. Delete it first.")
            else:
                result.subject = subject
                result.term = term
                result.academic_year = term.academic_year
                result.save()

                if ca_data:
                    assessments = json.loads(ca_data)

                    for item in assessments:
                        ContinuousAssessment.objects.create(
                            student=student,
                            subject=subject,
                            term=term,
                            academic_year=term.academic_year,
                            title=item['title'],
                            score=item['score']
                        )
                        

                messages.success(request, f"Result saved for {student.get_full_name()}")
                return redirect('accounts:teacher-upload-results', class_id=class_id, subject_id=subject_id)
        else:
            print(form.errors)
    else:
        form = ResultUploadForm(class_id=class_id)  # Only set form on GET

    return render(request, 'accounts/upload_result.html', {
        'school_class': school_class, 
        'subject': subject, 
        'students': User.objects.filter(role='student', school_class_id=class_id),
        'active_term': term,
        'form': form,
        'entered_results': entered_results,
    })

# --------------------------------
# UPLOAD RESULTS LIST NEW
# ------------------------------------
@login_required
def upload_results_list(request):
    assignments = TeacherSubjectClass.objects.filter(
        teacher=request.user
    )

    return render(request, 'accounts/upload_results_list.html', {
        'assignments': assignments
    })

# -----------------------------------
# TEACHER SEES RESULTS
# ------------------------------

@login_required
def teacher_results(request):
    if request.user.role != 'teacher':
        return redirect('accounts:dashboard')
    
    school = request.user.school
    
    active_term = Term.objects.filter(is_active=True, school=school).first()
    if not active_term:
        active_term = Term.objects.filter(school=school).first()
    
    if not active_term:
        messages.error(request, "No terms found. Create a term in Admin first.")
        return redirect('accounts:dashboard')
        
    # Get term number from GET, fallback to active term
    selected_term_number = request.GET.get('term')
    selected_year = request.GET.get('year')

    if selected_year:
        selected_year = int(selected_year)
    else:
        selected_year = active_term.academic_year.id

        selected_year = request.GET.get('year', active_term.academic_year.id)

    # Get the correct term object
    term_obj = Term.objects.filter(
        school=school, 
        term_number=selected_term_number, 
        academic_year_id=selected_year
    ).first()

    if not term_obj:
        term_obj = active_term

    # Get the name from DB here, once
    term_name = term_obj.get_term_number_display()


    academic_years = AcademicYear.objects.filter(
        term__school=school
    ).distinct().order_by('-name')
        
    assignments = TeacherSubjectClass.objects.filter(teacher=request.user)\
        .select_related('school_class', 'subject')
    classes_with_students = []
    
    for assignment in assignments:
        school_class = assignment.school_class
        subject = assignment.subject
        if not Result.objects.filter(
            subject=subject,
            term=term_obj,
            academic_year=term_obj.academic_year,
            student__school_class=school_class,
            status__in=['draft', 'returned', 'submitted']
        ).exists():
            continue

        students = User.objects.filter(role='student', school_class_id=school_class.id)

        student_cards = []

        for student in students:
            results = Result.objects.filter(
                student=student,
                subject_id=subject.id,
                term_id=term_obj.id,
                academic_year_id=term_obj.academic_year.id,
                status__in=['draft', 'returned', 'submitted']
            )

            total = sum(r.total_score for r in results)
            count = results.count()
            average = total / count if count else 0

            if average >= 80:
                grade, remark = "A", "Excellent"
            elif average >= 70:
                grade, remark = "B", "Very Good"
            elif average >= 60:
                grade, remark = "C", "Good"
            elif average >= 50:
                grade, remark = "D", "Pass"
            elif average >= 40:
                grade, remark = "E", "Weak"
            else:
                grade, remark = "F", "Fail"

            student_cards.append({
                'student': student,
                'results': results,
                'total': total,
                'average': average,
                'grade': grade,
                'remark': remark,
            })

        # ✅ MUST BE INSIDE LOOP
        if student_cards:
            draft_count = Result.objects.filter(
                student__school_class=school_class,
                subject=subject,
                term=term_obj,
                academic_year=term_obj.academic_year,
                status__in=['draft', 'returned']
            ).count()

            classes_with_students.append({
                'school_class': school_class,
                'subject': subject,
                'student_cards': student_cards,
                'draft_count': draft_count
            })
        
        
    return render(request, 'accounts/teacher_results.html', {
        'classes_with_students': classes_with_students,
        'selected_term': term_obj.term_number,
        'selected_year': term_obj.academic_year.id,
        'term_obj': term_obj,
        'term_name': term_name,
        'academic_years': academic_years, 
    })


@login_required
def teacher_published_results(request):
    if request.user.role != 'teacher':
        return redirect('accounts:dashboard')

    # subjects/classes assigned to teacher
    assignments = TeacherSubjectClass.objects.filter(
        teacher=request.user
    )

    results = Result.objects.filter(
        status='published',
        subject__in=[a.subject for a in assignments],
        student__school_class__in=[a.school_class for a in assignments]
    ).select_related(
        'student',
        'subject'
    ).order_by('-published_at')
# FILTERS
    class_id = request.GET.get('class')
    term = request.GET.get('term')
    year = request.GET.get('year')

    if class_id:
        results = results.filter(student__school_class_id=class_id)

    if term:
        results = results.filter(term__term_number=term)

    if year:
        results = results.filter(academic_year_id=year)

    # dropdown data
    classes = SchoolClass.objects.filter(
        teachersubjectclass__teacher=request.user
    ).distinct()

    years = AcademicYear.objects.all().order_by('-name')
    for r in results:
        total = float(r.class_score or 0) + float(r.exam_score or 0)


    setting = SchoolSetting.objects.first()
    attendance_mode = setting.attendance_mode if setting else "subject"

    return render(request, 'accounts/teacher_published_results.html', {
        'results': results,
        'classes': classes,
        'years': years,
        'attendance_mode': attendance_mode,
    })

def student_results_table_view(request):
    # This gives us rank, term_total, etc
    summaries = StudentTermSummary.objects.select_related(
        'student', 
        'school_class'
    ).filter(
        student__role='student'
    ).order_by(
        'school_class__name', 
        'rank', 
        'student__last_name'
    )
    
    students = []
    for summary in summaries:
        student_results = Result.objects.filter(
            student=summary.student
        ).select_related('subject').order_by('subject__name')
        
        students.append({
            'id': summary.student.id,
            'name': f"{summary.student.first_name} {summary.student.last_name}",
            'student_number': summary.student.student_number,
            'class_name': summary.school_class.name,
            'rank': summary.rank,
            'term_total': summary.term_total,
            'term_average': summary.term_average,
            'term_grade': summary.term_grade,
            'term_remark': summary.term_remark,
            'term': summary.term,
            'academic_year': summary.academic_year,
            'results': student_results  # This is the list of 10 subjects
        })
    
    context = {'students': students}
    return render(request, 'accounts/student_results_table.html', context)


# -----------------------------------
# TEACHER EDIT RESULTS
# ----------------------------------
@login_required
def edit_result(request, pk):
    result = get_object_or_404(Result, pk=pk)

    # Prevent editing submitted results
    if result.status not in ['draft', 'returned']:
        messages.error(request, "You can't edit this result. It was already submitted to admin.")
        return redirect('accounts:teacher-results')

    if request.method == 'POST':
        form = ResultForm(request.POST, instance=result)

        if form.is_valid():
            updated_result = form.save(commit=False)

            # 👇 VERY IMPORTANT: keep original student
            updated_result.student = result.student

            updated_result.save()

            messages.success(request, "Result updated. Don't forget to submit to admin again.")
            return redirect('accounts:teacher-results')

        else:
            print("FORM ERRORS:", form.errors)

    else:
        form = ResultForm(instance=result)

    return render(request, 'accounts/edit_result.html', {
        'form': form,
        'result': result
    })


@login_required
def submit_results_to_admin(request):
    print("=== POST DATA DEBUG ===")
    print(request.POST)
    print("====================")
    
    if request.user.role != 'teacher':
        return redirect('accounts:dashboard')
    
    if request.method != 'POST':
        messages.error(request, "Use the button to submit.")
        return redirect('accounts:teacher-results')
    
    class_id = request.POST.get('class_id')
    subject_id = request.POST.get('subject_id')
    term_id = request.POST.get('term')
    year_id = request.POST.get('year')

    # force integers safely
    term_id = int(term_id)
    year_id = int(year_id)
    
    if not all([class_id, subject_id, term_id, year_id]):
        messages.error(request, "Missing required information.")
        return redirect('accounts:teacher-results')
    
    # Security check
    if not TeacherSubjectClass.objects.filter(
        teacher=request.user, 
        subject_id=subject_id, 
        school_class_id=class_id
    ).exists():
        messages.error(request, "You are not assigned to this subject and class.")
        return redirect('accounts:teacher-results')
    
    # Filter using _id fields since term/year are FKs
    drafts = Result.objects.filter(
        subject_id=subject_id,
        student__school_class_id=class_id,
        term__term_number=term_id,
        academic_year_id=year_id,
        status__in=['draft', 'returned']
    )    
    
    count = drafts.count()
    if count == 0:
        messages.info(request, "No pending results to submit for this term.")
        return redirect(f"{reverse('accounts:teacher-results')}?year={year_id}&term={term_id}")
    
    drafts.update(status='submitted', submitted_at=timezone.now())
    messages.success(request, f"Submitted {count} result(s) to admin for review.")
    
    return redirect(f"{reverse('accounts:teacher-results')}?year={year_id}&term={term_id}")

# --------------------------------
# TEACHER MARK ATTENDANCE
# ---------------------------

@login_required
def mark_attendance(request):
    teacher = request.user
    school = teacher.school

    setting = SchoolSetting.objects.first()
    attendance_mode = setting.attendance_mode if setting else "subject"
    if attendance_mode == "class_teacher":
        if not getattr(teacher, 'is_class_teacher', False):
            messages.error(request, "Only class teachers can mark attendance in this mode.")
            return redirect('accounts:dashboard')

    tsc_options = TeacherSubjectClass.objects.filter(
        teacher=teacher,
        school_class__school=school
    ).select_related('subject', 'school_class')

    if attendance_mode == "class_teacher":
        tsc_options = TeacherSubjectClass.objects.filter(
            school_class__class_teacher=teacher,
            school_class__school=school
        ).select_related('subject', 'school_class')
        seen = set()
        unique = []
        for t in tsc_options:
            if t.school_class_id not in seen:
                seen.add(t.school_class_id)
                unique.append(t)
        tsc_options = unique

    selected_tsc_id = request.GET.get('tsc')
    school_class_id = request.GET.get('school_class')
    subject_id = request.GET.get('subject')
    selected_date = request.GET.get('date', date.today().isoformat())
    
    students = []
    tsc_obj = None
    session = None
    attendance_dict = {}
    min_date = None  
    max_date = None  
    
    if selected_tsc_id:
        if attendance_mode == "class_teacher":
            tsc_obj = get_object_or_404(TeacherSubjectClass, id=selected_tsc_id, school_class__class_teacher=teacher, school_class__school=school)
        else:
            tsc_obj = get_object_or_404(TeacherSubjectClass, id=selected_tsc_id, teacher=teacher, school_class__school=school)
    
    if tsc_obj:
        school_class_id = tsc_obj.school_class_id
        subject_id = tsc_obj.subject_id
        students = User.objects.filter(
            role='student',
            school_class_id=school_class_id,
            school=school
        ).order_by('last_name', 'first_name')
        
    if request.method == 'POST' and tsc_obj:
        selected_date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
        if selected_date_obj.weekday() >= 5:
            messages.error(request, "Cannot mark attendance on weekends.")
            return redirect('accounts:mark_attendance')
        
        is_holiday = AcademicCalendar.objects.filter(
            school=school,
            affects_timetable=True,
            start_date__lte=selected_date_obj,
            end_date__gte=selected_date_obj
        ).exists()
        
        if is_holiday:
            messages.error(request, f"Cannot save attendance. {selected_date_obj} is a holiday.")
            return redirect('accounts:mark_attendance')
        
        if attendance_mode == "class_teacher":
            first_tsc = TeacherSubjectClass.objects.filter(school_class_id=school_class_id).first()
            subject_id = first_tsc.subject_id if first_tsc else tsc_obj.subject_id
        else:
            subject_id = tsc_obj.subject_id

        session, created = AttendanceSession.objects.get_or_create(
            school_class_id=school_class_id,
            subject_id=subject_id,
            date=selected_date,
            teacher=teacher
        )
        
        marked_count = 0
        with transaction.atomic():
            for student in students:
                status = request.POST.get(f'status_{student.id}')
                # FINAL RULE: ONLY P and A ALLOWED, No Late
                if status not in ['P', 'A']:
                    status = None  # Treat invalid as not marked
                
                if status:
                    AttendanceRecord.objects.update_or_create(
                        session=session,
                        student=student,
                        defaults={'status': status}
                    )
                    marked_count += 1
                else:
                    AttendanceRecord.objects.filter(session=session, student=student).delete()
            
            marked_ids = [s.id for s in students if request.POST.get(f'status_{s.id}') in ['P', 'A']]
            unmarked_students = students.exclude(id__in=marked_ids)
            absent_count = unmarked_students.count()
            for student in unmarked_students:
                AttendanceRecord.objects.update_or_create(
                    session=session,
                    student=student,
                    defaults={'status': 'A'}  # Auto absent
                )
            
        messages.success(request, f"Attendance for {tsc_obj.school_class.name} saved. {marked_count} marked, {absent_count} auto-absent.")
        return redirect('accounts:attendance_detail', session_id=session.id)
    
    if tsc_obj:
        session = AttendanceSession.objects.filter(
            school_class_id=school_class_id,
            subject=tsc_obj.subject,
            date=selected_date,
            teacher=teacher
        ).first()
        
        if session:
            existing = AttendanceRecord.objects.filter(session=session)
            attendance_dict = {a.student_id: a.status for a in existing}
            min_date = (date.today() - timedelta(days=30)).isoformat()
            max_date = date.today().isoformat()
    
    context = {
        'tsc_options': tsc_options,
        'students': students,
        'selected_tsc': tsc_obj,
        'selected_tsc_id': selected_tsc_id,
        'selected_date': selected_date,
        'attendance_dict': attendance_dict,
        'session': session, 
        'min_date': min_date,  
        'max_date': max_date,  
        'today': date.today(),
        'attendance_mode': attendance_mode,
    }
    
    return render(request, 'accounts/mark_attendance.html', context)
# --------------------------------

# --------------------------------
@login_required
def attendance_mark(request, session_id):
    session = AttendanceSession.objects.select_related('school_class', 'subject').get(id=session_id)
    students = User.objects.filter(school_class=session.school_class, role='student', is_active=True).order_by('first_name')

    if not students.exists():
        messages.error(request, f"No students found in {session.school_class.name}. Add students first.")
        return redirect('accounts:attendance_create')

    if request.method == 'POST':
        for student in students:
            status = request.POST.get(f'status_{student.id}', '')
            AttendanceRecord.objects.update_or_create(
                session=session,
                student=student,
                defaults={'status': status}
            )
        messages.success(request, 'Attendance saved successfully!')
        return redirect('accounts:attendance_report')

    existing = {r.student_id: r.status for r in AttendanceRecord.objects.filter(session=session)}
    
    return render(request, 'accounts/attendance_mark.html', {
        'session': session,
        'students': students,
        'existing': existing
    })


@login_required
def attendance_report(request):
    teacher = request.user
    school = teacher.school

    setting = SchoolSetting.objects.filter(
        school=school
    ).first()

    attendance_mode = (
        setting.attendance_mode
        if setting else "subject"
    )

    # =========================================================
    # FILTERS
    # =========================================================

    selected_year_id = request.GET.get("academic_year")
    selected_term_id = request.GET.get("term")
    class_id = request.GET.get("class_id")

    # =========================================================
    # ACADEMIC YEARS
    # =========================================================

    academic_years = AcademicYear.objects.filter(
        school=school
    ).order_by("-start_date")

    # =========================================================
    # TERMS
    # =========================================================

    terms = Term.objects.filter(
        school=school
    ).select_related(
        "academic_year"
    ).order_by(
        "-academic_year__start_date",
        "term_number"
    )

    # If academic year is selected, only show its terms
    if selected_year_id:
        terms = terms.filter(
            academic_year_id=selected_year_id
        )

    # =========================================================
    # SELECT TERM
    # =========================================================

    active_term = None

    if selected_term_id:
        active_term = Term.objects.filter(
            id=selected_term_id,
            school=school
        ).first()

    # If no term selected, use selected academic year's active term
    if not active_term and selected_year_id:
        active_term = Term.objects.filter(
            school=school,
            academic_year_id=selected_year_id,
            is_active=True
        ).first()

    # Otherwise use current active term
    if not active_term:
        active_term = Term.objects.filter(
            school=school,
            is_active=True
        ).first()

    if not active_term:
        messages.error(
            request,
            "No academic term found."
        )
        return redirect(
            "accounts:teacher-dashboard"
        )

    # =========================================================
    # KEEP FILTER VALUES CORRECT
    # =========================================================

    selected_year_id = active_term.academic_year_id
    selected_term_id = active_term.id

    # =========================================================
    # WEEK FUNCTIONS
    # =========================================================

    def get_week_number(start_date, target_date):
        if target_date < start_date:
            return 1

        days = (
            target_date - start_date
        ).days

        return (
            (days + start_date.weekday()) // 7
        ) + 1

    def get_week_range(start_date, week_number):
        current_start = start_date

        for _ in range(week_number - 1):

            friday = current_start + timedelta(
                days=(4 - current_start.weekday())
            )

            current_start = (
                friday + timedelta(days=3)
            )

        week_start = current_start

        week_end = (
            week_start +
            timedelta(
                days=(4 - week_start.weekday())
            )
        )

        return week_start, week_end

    def get_total_weeks(start_date, end_date):

        week = 1
        current_start = start_date

        while current_start <= end_date:

            friday = current_start + timedelta(
                days=(4 - current_start.weekday())
            )

            current_start = (
                friday + timedelta(days=3)
            )

            week += 1

        return week - 1

    # =========================================================
    # SELECTED WEEK
    # =========================================================

    today = timezone.localdate()

    week_param = request.GET.get(
        "attendance_week"
    )

    if week_param:

        try:
            selected_week = int(
                week_param
            )

        except ValueError:

            selected_week = get_week_number(
                active_term.start_date,
                today
            )

    else:

        selected_week = get_week_number(
            active_term.start_date,
            today
        )

    total_weeks = get_total_weeks(
        active_term.start_date,
        active_term.end_date
    )

    selected_week = max(
        1,
        min(
            selected_week,
            total_weeks
        )
    )

    week_start, week_end = get_week_range(
        active_term.start_date,
        selected_week
    )

    prev_week = max(
        1,
        selected_week - 1
    )

    next_week = min(
        total_weeks,
        selected_week + 1
    )

    # =========================================================
    # TEACHER CLASSES
    # =========================================================

    if (
        attendance_mode == "class_teacher"
        and teacher.role == "teacher"
    ):

        teacher_classes = SchoolClass.objects.filter(
            class_teacher=teacher,
            school=school
        ).order_by("name")

    elif teacher.role == "teacher":

        teacher_classes = SchoolClass.objects.filter(
            teachersubjectclass__teacher=teacher,
            school=school
        ).distinct().order_by("name")

    else:

        teacher_classes = SchoolClass.objects.filter(
            school=school
        ).order_by("name")

    # =========================================================
    # CLASS FILTER
    # =========================================================

    student_filter_classes = teacher_classes

    if class_id:
        student_filter_classes = (
            teacher_classes.filter(
                id=class_id
            )
        )

    # =========================================================
    # TOTAL STUDENTS
    # =========================================================

    total_students = User.objects.filter(
        role="student",
        is_active=True,
        school=school,
        school_class__in=student_filter_classes
    ).distinct().count()

    # =========================================================
    # REPORT
    # =========================================================

    report = {}

    # =========================================================
    # CLASS TEACHER ATTENDANCE
    # =========================================================

    if attendance_mode == "class_teacher":

        classes_qs = teacher_classes

        if class_id:
            classes_qs = classes_qs.filter(
                id=class_id
            )

        for school_class in classes_qs:

            class_sessions = (
                AttendanceSession.objects.filter(
                    date__range=[
                        week_start,
                        week_end
                    ],
                    school_class=school_class,
                    school=school
                )
                .order_by("date")
            )

            if not class_sessions.exists():
                continue

            records = (
                AttendanceRecord.objects.filter(
                    session__in=class_sessions
                )
                .values(
                    "student_id",
                    "status"
                )
            )

            by_student = defaultdict(list)

            for record in records:

                by_student[
                    record["student_id"]
                ].append(
                    record["status"]
                )

            present_count = sum(
                1
                for statuses in by_student.values()
                if "P" in statuses
            )

            absent_count = sum(
                1
                for statuses in by_student.values()
                if "P" not in statuses
            )

            base = class_sessions.first()

            base.present = present_count
            base.absent = absent_count
            base.total = (
                present_count +
                absent_count
            )

            report[school_class] = [
                base
            ]

    # =========================================================
    # SUBJECT ATTENDANCE
    # =========================================================

    else:

        sessions = (
            AttendanceSession.objects
            .select_related(
                "school_class",
                "subject",
                "teacher"
            )
            .filter(
                date__range=[
                    week_start,
                    week_end
                ],
                school=school
            )
        )

        if teacher.role == "teacher":

            sessions = sessions.filter(
                teacher=teacher
            )

        if class_id:

            sessions = sessions.filter(
                school_class_id=class_id
            )

        sessions = (
            sessions
            .annotate(
                total=Count(
                    "records"
                ),
                present=Count(
                    "records",
                    filter=Q(
                        records__status="P"
                    )
                ),
                absent=Count(
                    "records",
                    filter=Q(
                        records__status="A"
                    )
                )
            )
            .filter(
                total__gt=0
            )
            .order_by(
                "school_class__name",
                "subject__name",
                "date"
            )
        )

        for school_class, class_sessions in groupby(
            sessions,
            key=lambda x: x.school_class
        ):

            report[school_class] = list(
                class_sessions
            )

    # =========================================================
    # CLASS COUNT
    # =========================================================

    teacher_classes_count = (
        teacher_classes.count()
    )

    if class_id:

        teacher_classes_count = (
            teacher_classes
            .filter(id=class_id)
            .count()
        )

    # =========================================================
    # TOTAL PRESENT / ABSENT
    # =========================================================

    total_present = 0
    total_absent = 0

    for sessions in report.values():

        for session in sessions:

            total_present += getattr(
                session,
                "present",
                0
            )

            total_absent += getattr(
                session,
                "absent",
                0
            )

    # =========================================================
    # TEMPLATE
    # =========================================================

    return render(
        request,
        "accounts/attendance_report.html",
        {
            "report": report,

            "total_students": total_students,

            "all_classes": teacher_classes,

            "selected_class_id": (
                int(class_id)
                if class_id
                else None
            ),

            "attendance_mode": attendance_mode,

            # Academic year / term filters
            "academic_years": academic_years,
            "terms": terms,

            "selected_year_id": (
                int(selected_year_id)
                if selected_year_id
                else None
            ),

            "selected_term_id": (
                int(selected_term_id)
                if selected_term_id
                else None
            ),

            "selected_term": active_term,

            # Week
            "attendance_week_number": selected_week,

            "attendance_week_start": week_start,

            "attendance_week_end": week_end,

            "attendance_previous_week": prev_week,

            "attendance_next_week": next_week,

            "total_weeks": total_weeks,

            # Summary
            "teacher_classes_count": (
                teacher_classes_count
            ),

            "total_present": total_present,

            "total_absent": total_absent,
        }
    )

@login_required
def attendance_detail(request, session_id):
    school = request.user.school
    setting = SchoolSetting.objects.filter(
        school=school
    ).first()  # ✅ FIX 1 - filter by school
    attendance_mode = setting.attendance_mode if setting else "subject"
    user = request.user
    is_school_admin = getattr(user, "role", "") in [
        "admin",
        "proprietor",
        "school_admin",
        "director",
    ]

    # Get base session
    if user.is_staff or user.is_superuser:
        base_session = get_object_or_404(
            AttendanceSession, id=session_id, school=school
        )
    elif is_school_admin:
        base_session = get_object_or_404(
            AttendanceSession, id=session_id, school=school
        )  # ✅ FIX 2 - use school
    else:
        if attendance_mode == "class_teacher":
            base_session = get_object_or_404(
                AttendanceSession,
                id=session_id,
                school=school,
                school_class__class_teacher=user,
            )
        else:
            base_session = get_object_or_404(
                AttendanceSession, id=session_id, teacher=user, school=school
            )

    # === CLASS TEACHER MODE: MERGE ALL SUBJECTS OF THAT DAY ===
    if attendance_mode == "class_teacher":
        all_sessions = (
            AttendanceSession.objects.filter(
                date=base_session.date,
                school_class=base_session.school_class,
                school=school,
            )
            .annotate(total=Count("records"))
            .filter(total__gt=0)
        )  # ✅ added school
        all_records = AttendanceRecord.objects.filter(
            session__in=all_sessions
        ).select_related("student")

        by_student = defaultdict(list)
        student_objs = {}
        for r in all_records:
            by_student[r.student_id].append(r.status)
            student_objs[r.student_id] = r.student

        merged_list = []
        for sid, statuses in by_student.items():
            final = "P" if "P" in statuses else "A"

            class FakeRec:
                pass

            fr = FakeRec()
            fr.student = student_objs[sid]
            fr.status = final
            fr.breakdown = statuses
            merged_list.append(fr)

        present_count = sum(1 for s in by_student.values() if "P" in s)
        absent_count = sum(1 for s in by_student.values() if "P" not in s)
        total_count = len(by_student)
        attendance_rate = (present_count / total_count * 100) if total_count else 0

        paginator = Paginator(merged_list, 25)
        page_obj = paginator.get_page(request.GET.get("page"))

    else:
        records = (
            AttendanceRecord.objects.filter(session=base_session)
            .select_related("student")
            .order_by("student__last_name")
        )
        total_count = records.count()
        present_count = records.filter(status="P").count()
        absent_count = records.filter(status="A").count()
        attendance_rate = (present_count / total_count * 100) if total_count else 0
        paginator = Paginator(records, 25)
        page_obj = paginator.get_page(request.GET.get("page"))

    tsc_obj = TeacherSubjectClass.objects.filter(
        school_class=base_session.school_class
    ).first()


    return render(
        request,
        "accounts/attendance_detail.html",
        {
            "session": base_session,
            "page_obj": page_obj,
            "total_count": total_count,
            "present_count": present_count,
            "absent_count": absent_count,
            "late_count": 0,
            "attendance_rate": round(attendance_rate, 2),
            "tsc_obj": tsc_obj,
            "attendance_mode": attendance_mode,
        },
    )


@login_required
def attendance_create_session(request):
    if request.method == 'POST':
        school_class_id = request.POST.get('school_class')
        subject_id = request.POST.get('subject')
        session_date = request.POST.get('date')  # renamed this
        
        school_class = SchoolClass.objects.get(id=school_class_id)
        subject = Subject.objects.get(id=subject_id)
        
        session, created = AttendanceSession.objects.get_or_create(
            date=session_date,  # use renamed variable
            school_class=school_class,
            subject=subject,
            teacher=request.user
        )
        return redirect('accounts:attendance_mark', session_id=session.id)
    
    classes = SchoolClass.objects.all()
    subjects = Subject.objects.all()
    return render(request, 'accounts/attendance_create.html', {
        'classes': classes,
        'subjects': subjects,
        'today': dt_date.today().strftime('%Y-%m-%d')  # fixed this line
    })


# TEACHER STUDENTS LIST
# --------------------------
@login_required
def teacher_student_list(request):
    if request.user.role != "teacher":
        return redirect('accounts:dashboard')
    return render(request, 'accounts/teacher_student_list.html')

# ---------------------------
# TEACHER ATTENDANCE
# --------------------------
@login_required
def teacher_attendance_report(request):
    if request.user.role != "teacher":
        return redirect('accounts:dashboard')
    return render(request, 'accounts/teacher_attendance_report.html')

# ---------------------------
# TEACHER ASSIGNMENTS
# --------------------------
@login_required
def teacher_assignments(request):
    assignments = TeacherSubjectClass.objects.filter(teacher=request.user)

    return render(request, 'accounts/teacher_assignments.html', {
        'assignments': assignments
    })


# -------------------------------

# ----------------------------
@login_required
def school_settings(request):
    if request.user.role != 'admin':
        messages.error(request, "Only school owners can access this page.")
        return redirect('accounts:dashboard')
    
    school = request.user.school
    setting, _ = SchoolSetting.objects.get_or_create(school=school)

    # Handle school settings update - CREATE if missing
    if request.method == 'POST' and 'update_school' in request.POST:
        name = request.POST.get('name')
        address = request.POST.get('address')
        phone = request.POST.get('phone')
        email = request.POST.get('email')
        gps_address = request.POST.get('gps_address')
        logo = request.FILES.get('logo')
        can_teachers_manage_students = 'can_teachers_manage_students' in request.POST
        attendance_mode = request.POST.get('attendance_mode')

        setting.attendance_mode = attendance_mode
        setting.save()

        if not school:
            # Create school and link it to user
            school = School.objects.create(
                name=name,
                address=address,
                phone=phone,
                email=email,
                gps_address=gps_address,
                logo=logo,
                can_teachers_manage_students=can_teachers_manage_students
            )
            request.user.school = school
            request.user.save()
        else:
            # Update existing school
            school.name = name
            school.address = address
            school.phone = phone
            school.email = email
            school.gps_address = gps_address
            school.can_teachers_manage_students = can_teachers_manage_students
            if logo:
                school.logo = logo
            school.save()

        messages.success(request, "School settings saved.")
        return redirect('accounts:school_settings')

    fee_breakdown = None
    selected_year = None
    selected_term = request.GET.get('term')

    if selected_term:
        try:
            selected_term = int(selected_term)
        except ValueError:
            selected_term = None
    years = []
    active_term = None
    fee_structures = []
    existing_fee_keys = set()

    if school:
        fee_breakdown = FeeStructure.objects.filter(school=school).order_by('-id').first()
        selected_year = request.GET.get('year') or (fee_breakdown.academic_year if fee_breakdown else None)
        years = FeeStructure.objects.filter(school=school).values_list('academic_year', flat=True).distinct().order_by('-academic_year')
        active_term = Term.objects.filter(
            school=school,
            is_active=True
        ).order_by('-id').first()
        
        if not selected_year:
            selected_year = active_term.academic_year if active_term else (years.first() if years else '')
        
        fee_structures = FeeStructure.objects.filter(school=school, academic_year=selected_year)
        if selected_term:
         fee_structures = fee_structures.filter(term_id=selected_term)
        fee_structures = fee_structures.order_by('term', 'stage')
        existing_fee_keys = {f"{fs.stage}:{fs.term}" for fs in fee_structures}

    # Handle creating fee structure - only if school exists
    if request.method == 'POST' and 'create_fee_structure' in request.POST:
        if not school:
            messages.error(request, "Please create your school first before adding fee structures.")
            return redirect('accounts:school_settings')
            
        term_id = request.POST.get('term')
        term = Term.objects.filter(id=term_id, school=school).first()
        if not term:
            messages.error(request, "Invalid term selected.")
            return redirect('accounts:school_settings')
        
        academic_year = request.POST.get('academic_year')
        
        def to_decimal(val):
            try:
                return Decimal(val) if val not in [None, ''] else Decimal('0')
            except:
                return Decimal('0')
        
        school_fees = to_decimal(request.POST.get('school_fees'))
        pta_dues = to_decimal(request.POST.get('pta_dues'))
        computer_levy = to_decimal(request.POST.get('computer_levy'))
        exam_fees = to_decimal(request.POST.get('exam_fees'))
        boarding_fee = to_decimal(request.POST.get('boarding_fee'))
        hostel_fee = to_decimal(request.POST.get('hostel_fee'))
        development_fee = to_decimal(request.POST.get('development_fee'))
        canteen_amount = to_decimal(request.POST.get('canteen_amount'))
        other_fees = to_decimal(request.POST.get('other_fees'))
        total = (school_fees + pta_dues + computer_levy + exam_fees + boarding_fee + hostel_fee + development_fee + canteen_amount + other_fees)
        
        obj, created = FeeStructure.objects.update_or_create(
            school=school,
            term=term,
            academic_year=academic_year,
            defaults={
                'school_fees': school_fees,
                'pta_dues': pta_dues,
                'computer_levy': computer_levy,
                'exam_fees': exam_fees,
                'other_fees': other_fees,
                'hostel_fee': hostel_fee,
                'boarding_fee': boarding_fee,
                'development_fee': development_fee,
                'canteen_amount': canteen_amount,
                'total_amount': total,
                'is_active': True
            }
        )
        
        msg = "created" if created else "updated"
        messages.success(request, f"Fee structure {msg} for {obj.term.term_number} {academic_year}.")
        
        query = f"?year={academic_year}"
        if term:
            query += f"&term={term.id}"
        return redirect(f"{request.path}{query}")
    terms = Term.objects.filter(school=school).order_by('term_number')
    
    return render(request, 'accounts/school_settings.html', {
        'school': school,
        'fee_structures': fee_structures,
        'fee_structures_count': len(fee_structures),
        'years': years,
        'selected_year': selected_year,
        'selected_term': selected_term,
        'active_term': active_term,
        'terms': terms, 
        'setting': setting,
        'fee_breakdown': fee_breakdown,
        'existing_fee_keys': existing_fee_keys, 
    })


@login_required
def assign_students_to_class(request):
    # Step 3A: Check if this school allows teachers to do this
    if not request.user.school.can_teachers_manage_students:
        messages.error(request, "Your school does not allow teachers to assign students to classes.")
        return redirect('teacher_attendance')  # send them back if not allowed
    
    # Step 3B: Get all classes this teacher teaches
    tsc_list = TeacherSubjectClass.objects.filter(teacher=request.user)
    
    selected_tsc = None
    assigned_ids = []
    # Step 3C: Get all students from the same school as the teacher
    students = User.objects.filter(role='student', school=request.user.school).order_by('first_name', 'last_name')
    
    # Step 3D: Check if teacher selected a class from the dropdown
    tsc_id = request.GET.get('tsc')
    if tsc_id:
        selected_tsc = get_object_or_404(TeacherSubjectClass, id=tsc_id, teacher=request.user)
        
        # Step 3E: If teacher clicked "Save", update the database
        if request.method == 'POST':
            checked_ids = request.POST.getlist('student_ids')  # list of checked student IDs
            
            # Delete old assignments for this class+subject first
            StudentSubjectClass.objects.filter(
                school_class=selected_tsc.school_class, 
                subject=selected_tsc.subject
            ).delete()
            
            # Create new assignments for each checked student
            for sid in checked_ids:
                StudentSubjectClass.objects.create(
                    student_id=sid,
                    school_class=selected_tsc.school_class,
                    subject=selected_tsc.subject
                )
            messages.success(request, "Student list updated successfully.")
            return redirect(f'{request.path}?tsc={tsc_id}')
        
        # Step 3F: Get IDs of students already assigned to this class+subject
        assigned_ids = StudentSubjectClass.objects.filter(
            school_class=selected_tsc.school_class,
            subject=selected_tsc.subject
        ).values_list('student_id', flat=True)

    # Step 3G: Send data to the HTML template
    return render(request, 'accounts/assign_students_to_class.html', {
        'tsc_list': tsc_list,
        'selected_tsc': selected_tsc,
        'students': students,
        'assigned_ids': list(assigned_ids),
    })


@login_required
def view_students_by_class(request):
    
    school = request.user.school
    
    # This adds student_count to each class, even if it's 0
    classes = SchoolClass.objects.filter(
        school=school
    ).annotate(
        student_count=Count('students', filter=Q(students__role='student'))
    ).order_by('name')


    grouped = {}

    for c in classes:

        match = re.match(r"(.+\d+)([A-Z]?)$", c.name.strip())

        if match:
            base_class = match.group(1)
            section = match.group(2)
        else:
            base_class = c.name
            section = ""

        stage = STAGE_LABELS.get(c.stage, c.stage)

        grouped.setdefault(stage, {})
        grouped[stage].setdefault(base_class, {
            "main": None,
            "sections": []
        })

        # MAIN CLASS (no letter)
        if section == "":
            grouped[stage][base_class]["main"] = c
        else:
            grouped[stage][base_class]["sections"].append(c)
    for stage, class_dict in grouped.items():

        for base_class, data in class_dict.items():

            count = 0

            if data["main"]:
                count += data["main"].student_count

            for section in data["sections"]:
                count += section.student_count

            data["student_count"] = count
    total_classes = 0

    for stage, class_dict in grouped.items():

            for base_class, data in class_dict.items():

                # If class has sections like A/B
                has_real_sections = any(
                    section.name.strip() != base_class.strip()
                    for section in data["sections"]
                )

                # Count only sections
                if has_real_sections:
                    total_classes += len(data["sections"])

                # Standalone class
                else:
                    total_classes += 1
    total_students = User.objects.filter(school=school, role='student').count()
    
    return render(request, 'accounts/view_students_by_class.html', {
        'grouped': grouped,
        'total_classes': total_classes,
        'total_students': total_students,
    })


@login_required  
def view_students_in_class(request, class_id):
    credentials = request.session.pop("credentials", None)
    school_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
    
    students_in_class = User.objects.filter(
        school_class=school_class, 
        role='student', 
        is_active=True
    )
    
    if request.method == 'POST' and request.POST.get('add_to_all'):
        subject_ids = request.POST.getlist('subject_ids')
        
        for student in students_in_class:
            for subject_id in subject_ids:
                StudentSubjectClass.objects.get_or_create(
                    student=student,
                    subject_id=subject_id,
                    school_class=school_class
                )
        messages.success(request, 'Subjects added to all students!')
        return redirect('accounts:view-students-in-class', class_id=school_class.id)
    
    if request.method == 'POST' and request.POST.get('student_id'):
        student_id = request.POST.get('student_id')
        subject_ids = request.POST.getlist('subject_ids')
        student = get_object_or_404(User, id=student_id, role='student', school=request.user.school, is_active=True)
        
        for subject_id in subject_ids:
            StudentSubjectClass.objects.get_or_create(
                student=student,
                subject_id=subject_id,
                school_class=school_class
            )
        messages.success(request, f'Subjects added to {student.first_name}!')
        return redirect('accounts:view-students-in-class', class_id=school_class.id)
    
    # Pagination
    students_list = students_in_class.order_by('last_name', 'first_name')
    paginator = Paginator(students_list, 4) 
    page_number = request.GET.get('page')
    students = paginator.get_page(page_number)
    
    all_subjects = []
    for subject in Subject.objects.filter(school=request.user.school):
        # Check if ALL students in class already have this subject
        students_with_subject = StudentSubjectClass.objects.filter(
            student__in=students_in_class, 
            subject=subject
        ).count()
        
        # Only add to modal if at least 1 student is missing it
        if students_with_subject < students_in_class.count():
            all_subjects.append(subject)
    
    return render(request, 'accounts/manage_students.html', {  
        'students': students,
        'school_class': school_class,
        'all_subjects': all_subjects,  
        'credentials': credentials
    })


@login_required
def deactivated_students(request, class_id):

    school = request.user.school

    school_class = get_object_or_404(
        SchoolClass,
        id=class_id,
        school=school
    )

    students = User.objects.filter(
        school=school,
        school_class=school_class,
        role='student',
        is_active=False
    )

    return render(request, "accounts/deactivated_students.html", {
        "students": students,
        "school_class": school_class,
    })

@login_required
def reactivate_student(request, student_id):

    if request.user.role != "admin":
        return redirect('accounts:dashboard')

    student = get_object_or_404(
        User,
        id=student_id,
        role='student',
        school=request.user.school
    )

    student.is_active = True
    student.save()

    messages.success(
        request,
        f"{student.get_full_name()} has been reactivated."
    )

    if student.school_class:
        return redirect(
            'accounts:view-students-in-class',
            class_id=student.school_class.id
        )

    return redirect('accounts:manage-students')
# ----------------------------
# STUDENT VIEWS
# ----------------------------

@login_required
def view_student_subjects(request):
    if request.user.role != 'student':
        return redirect('accounts:teacher_dashboard')
    
    student = request.user
    
    # Get ALL subjects this student is enrolled in, not just ones with results
    subjects = StudentSubjectClass.objects.filter(student=student)\
        .select_related('subject', 'school_class')\
        .order_by('subject__name')
    
    context = {
        'subjects': subjects,
        'student': student,
        
    }
    return render(request, 'accounts/view_student_subjects.html', context)


@login_required
def student_subject_progress(request, subject_id):
    if request.user.role != 'student':
        return redirect('accounts:dashboard')
    
    subject = get_object_or_404(Subject, id=subject_id)
    
    # Get all submitted results for this student + subject, ordered by term/year
    results = Result.objects.filter(
        student=request.user, 
        subject=subject, 
        status='submitted'
    ).order_by('academic_year', 'term')
    
    # Prepare data for chart
    labels = []
    data = []
    
    for result in results:
        term_name = dict(Result.TERM_CHOICES).get(result.term, f"Term {result.term}")
        labels.append(f"{term_name} {result.academic_year}")
        data.append(float(result.percentage))  # CHANGED: was result.score
    
    context = {
        'subject': subject,
        'labels': labels,
        'data': data,
        'results': results,  # So template can show the table too
    }
    return render(request, 'accounts/student_subject_progress.html', context)


@login_required
def view_student_results(request):
    if request.user.role != 'student':
        return redirect('accounts:teacher-dashboard')

    student = request.user
    # MULTI-SCHOOL FIX: Only show years/terms from student's school
    years = AcademicYear.objects.filter(school=student.school).order_by('-name')

    active_term = Term.objects.filter(
        is_active=True,
        school=student.school
    ).select_related('academic_year').first()

    term_number = request.GET.get('term')
    year_id = request.GET.get('year')

    if not term_number and not year_id:
        if active_term:
            term_number = str(active_term.term_number)
            year_id = str(active_term.academic_year.id)
        else:
            latest_year = years.first()
            if latest_year:
                year_id = str(latest_year.id)
                term_number = "1"

    try:
        term_number_int = int(term_number) if term_number else None
    except:
        term_number_int = None

    selected_term = None
    if term_number_int and year_id:
        selected_term = Term.objects.filter(
            term_number=term_number_int,
            academic_year_id=year_id,
            school=student.school
        ).select_related('academic_year').first()
    elif term_number_int:
        selected_term = Term.objects.filter(
            term_number=term_number_int,
            school=student.school
        ).select_related('academic_year').order_by('-academic_year__name').first()
        if selected_term:
            year_id = str(selected_term.academic_year.id)

    if not selected_term:
        messages.error(request, "No term found. Contact admin.")
        return render(request, 'accounts/view_student_results.html', {
            'results': [], 'years': years, 'active_term': active_term,
            'selected_year': year_id or "", 'selected_term': term_number or "",
        })

    results = Result.objects.filter(
        student=student,
        term__term_number=selected_term.term_number,
        academic_year_id=selected_term.academic_year.id,
        status='published'  
    ).select_related('subject', 'term').order_by('subject__code')
    results = list(results)

    def ca_max(t): return float(getattr(t, 'ca_total', 0) or 0)
    def exam_max(t): return float(getattr(t, 'exam_total', 0) or 0)

    grand_class_score = sum(float(r.class_score or 0) for r in results)
    grand_exam_score  = sum(float(r.exam_score or 0) for r in results)
    grand_class_max   = sum(ca_max(r.term) for r in results)
    grand_exam_max    = sum(exam_max(r.term) for r in results)

    grand_total_score = grand_class_score + grand_exam_score
    grand_total_max   = grand_class_max + grand_exam_max
    percentage = round(grand_total_score / grand_total_max * 100, 1) if grand_total_max else 0

    overall_grade = calculate_grade(percentage)
    overall_remark = ''
    position, total_students = get_class_position(student, selected_term.term_number, selected_term.academic_year.id)

    term_name = getattr(selected_term, 'name', f"Term {selected_term.term_number}")
    summary_qs = StudentTermSummary.objects.filter(student=student, term=term_name, academic_year_id=selected_term.academic_year.id)
    summary = summary_qs.first()
    if summary:
        if summary.term_total:
            grand_total_score = float(summary.term_total)
        if summary.term_average:
            percentage = float(summary.term_average)
        if summary.term_grade:
            overall_grade = summary.term_grade
        if summary.term_remark:
            overall_remark = summary.term_remark
        if summary.rank:
            position = summary.rank
        if summary.school_class_id:
            total_students = StudentTermSummary.objects.filter(
                school_class=summary.school_class,
                term=summary.term,
                academic_year=summary.academic_year
            ).count() or total_students

    for r in results:
        ca = float(r.term.ca_total or 0)
        ex = float(r.term.exam_total or 0)
        r.class_max = ca
        r.exam_max = ex
        r.total_max = ca + ex
        r.total = float(r.class_score or 0) + float(r.exam_score or 0)

    if not overall_remark:
        remark_map = {'A': 'Excellent','B': 'Very Good','C': 'Good','D': 'Credit','E': 'Pass','F': 'Fail'}
        overall_remark = remark_map.get(overall_grade, '')

    context = {
        'results': results,
        'school': student.school,
        'term': str(selected_term.term_number),
        'year': str(selected_term.academic_year.id),
        'selected_year': str(selected_term.academic_year.id),
        'selected_term': str(selected_term.term_number),
        'years': years,
        'terms': Term.objects.filter(school=student.school).select_related('academic_year'),
        'active_term': selected_term,
        'current_term_display': f"{selected_term.get_term_number_display()} - Academic Year: {selected_term.academic_year}",
        'student': student,
        'average_score': percentage,
        'overall_grade': overall_grade,
        'overall_remark': overall_remark,
        'position': position,
        'total_students': total_students,
        'grand_class_score': grand_class_score,
        'grand_class_max': grand_class_max,
        'grand_exam_score': grand_exam_score,
        'grand_exam_max': grand_exam_max,
        'grand_total_score': grand_total_score,
        'grand_total_max': grand_total_max,
    }
    return render(request, 'accounts/view_student_results.html', context)


from django.db.models import ExpressionWrapper, FloatField, Value

def get_class_position(student, term_number, year_id):
    if not student.school_class:
        return 0, 0

    school = student.school_class.school

    all_students_qs = User.objects.filter(
        school_class=student.school_class,
        school=school,
        role='student',
        is_active=True
    )
    total_students = all_students_qs.count()
    if total_students == 0:
        return 0, 0

    students_list = []

    for s in all_students_qs:
        results = Result.objects.filter(
            student=s,
            term__term_number=term_number,
            academic_year_id=year_id,
            status='published'
        ).select_related('term')

        if not results.exists():
            students_list.append((s.id, 0))
            continue

        # DYNAMIC - No fixed number, sum from actual results
        total_score = 0
        total_max = 0
        for r in results:
            total_score += float(r.class_score or 0) + float(r.exam_score or 0)
            total_max += float(r.term.ca_total or 0) + float(r.term.exam_total or 0)

        avg_pct = (total_score / total_max * 100) if total_max > 0 else 0
        students_list.append((s.id, avg_pct))

    # Sort by percentage
    students_list.sort(key=lambda x: (-x[1], x[0]))

    for idx, (student_id, avg) in enumerate(students_list, 1):
        if student_id == student.id:
            return idx, total_students

    return 0, total_students


@login_required
def view_student_fees(request):
    student_obj = getattr(request.user, 'student', None)
    student = student_obj if student_obj else request.user
    school = getattr(student, 'school', None)

    years = AcademicYear.objects.filter(
        id__in=Term.objects.filter(school=school).values_list('academic_year_id', flat=True)
    ).distinct().order_by('-name') if school else AcademicYear.objects.none()
    
    terms = Term.objects.filter(school=school).select_related('academic_year') if school else Term.objects.none()

    year_id = request.GET.get('year')
    term_number = request.GET.get('term')

    fees_qs = StudentFee.objects.filter(student=student).select_related('term').order_by('-academic_year')

    if year_id:
        try:
            year_obj = AcademicYear.objects.get(id=year_id)
            fees_qs = fees_qs.filter(academic_year=year_obj.name)
        except:
            pass
    if term_number:
        fees_qs = fees_qs.filter(term__term_number=term_number)

    latest_fee = fees_qs.first()
    transactions = PaymentTransaction.objects.filter(student=student)
    
    if year_id:
        transactions = transactions.filter(academic_year_id=year_id)
    if term_number:
        transactions = transactions.filter(term__term_number=term_number)

    real_total_paid = transactions.aggregate(total=Sum('total_amount'))['total'] or 0

    FEE_ITEM_FIELDS = [
        ('School Fees', 'school_fees', 'amount_paid_school_fees'),
        ('PTA Dues', 'pta_dues', 'amount_paid_pta_dues'),
        ('Boarding Fee', 'boarding_fee', 'amount_paid_boarding_fee'),
        ('Hostel Fee', 'hostel_fee', 'amount_paid_hostel_fee'),
        ('Development Fee', 'development_fee', 'amount_paid_development_fee'),
        ('Computer Levy', 'computer_levy', 'amount_paid_computer_levy'),
        ('Exam Fees', 'exam_fees', 'amount_paid_exam_fees'),
        ('Feeding Fee', 'canteen_amount', 'amount_paid_canteen'),
        ('Other Fees', 'other_fees', 'amount_paid_other_fees'),
    ]

    real_balance = 0
    if latest_fee:
        latest_fee.breakdown_items = []
        for label, due_field, _ in FEE_ITEM_FIELDS:
            value = getattr(latest_fee, due_field, 0)
            if value and value > 0:
                latest_fee.breakdown_items.append({'label': label, 'value': value})

        paid_by_type = PaymentItem.objects.filter(transaction__in=transactions).values('fee_name').annotate(total=Sum('amount'))
        paid_map = {p['fee_name']: float(p['total']) for p in paid_by_type}

        latest_fee.payment_options = []
        for label, due_field, paid_field in FEE_ITEM_FIELDS:
            amount_due = getattr(latest_fee, due_field, 0) or 0
            amount_paid = paid_map.get(label, 0)
            balance = float(amount_due) - amount_paid
            if amount_due > 0 and balance > 0:
                latest_fee.payment_options.append({'label': label, 'balance': balance, 'paid_field': paid_field})

        real_balance = float(latest_fee.total_amount) - float(real_total_paid)
        if real_balance < 0:
            real_balance = 0
        if not latest_fee.payment_options and real_balance > 0:
            latest_fee.payment_options.append({'label': 'Outstanding Balance', 'balance': real_balance, 'paid_field': 'amount_paid'})

    context = {
        'fees': fees_qs,
        'latest_fee': latest_fee,
        'total_fee': latest_fee.total_amount if latest_fee else 0,
        'balance': real_balance if latest_fee else 0,
        'amount_paid': real_total_paid if latest_fee else 0,
        'school': school,
        'years': years,
        'terms': terms,
        'selected_year': year_id or "",
        'selected_term': term_number or "",
    }
    return render(request, 'accounts/view_student_fees.html', context)


# ----------------------------
# PARENT VIEWS
# ----------------------------
@login_required
def view_child_results(request):
    if request.user.role!= "parent":
        return redirect('accounts:dashboard')

    links = ParentStudentLink.objects.filter(parent=request.user).select_related(
        'student','student__school_class','student__school_class__school', 'student__school'
    )
    children = [link.student for link in links]
    children_count = len(children)

    child_id = request.GET.get('child_id') or request.session.get('selected_child_id')
    selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
    if not selected_child and children:
        selected_child = children[0]
    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    school = selected_child.school_class.school if selected_child and selected_child.school_class and hasattr(selected_child.school_class, 'school') else None
    if not school:
        school = getattr(selected_child, 'school', None)

    selected_year_id = request.GET.get('year')
    term_id = request.GET.get('term_id') or request.GET.get('term')

    years = []
    all_terms = Term.objects.none()

    if selected_child and school:
        years = AcademicYear.objects.filter(school=school).order_by('-name')

        # FIXED: Term has no student field, only filter via result
        all_terms_qs = Term.objects.filter(
            school=school,
            result__student=selected_child,
            result__status='published'
        ).distinct()

        if selected_year_id:
            all_terms_qs = all_terms_qs.filter(academic_year_id=selected_year_id)

        all_terms = all_terms_qs.order_by('-start_date')

    selected_term = all_terms.filter(id=term_id).first() if term_id else all_terms.first()

    results = []
    summary = None
    term_data = None
    remark = None

    if selected_child and selected_term:
        results = Result.objects.filter(
            student=selected_child,
            term=selected_term,
            status='published'
        ).select_related('subject', 'term', 'term__academic_year').order_by('subject__name')

        for r in results:
            ca_val = float(ContinuousAssessment.objects.filter(
                student=selected_child, subject=r.subject, term=selected_term
            ).aggregate(total=Sum('score'))['total'] or r.class_score or 0)
            ex_val = float(r.exam_score or 0)
            r.calc_total = ca_val + ex_val
            r.calc_ca = ca_val
            r.calc_exam = ex_val
            if r.calc_total >= 80: r.calc_grade, r.calc_remark = "A", "Excellent"
            elif r.calc_total >= 70: r.calc_grade, r.calc_remark = "B", "Very Good"
            elif r.calc_total >= 60: r.calc_grade, r.calc_remark = "C", "Good"
            elif r.calc_total >= 50: r.calc_grade, r.calc_remark = "D", "Pass"
            elif r.calc_total >= 40: r.calc_grade, r.calc_remark = "E", "Weak"
            else: r.calc_grade, r.calc_remark = "F", "Fail"

        remark = StudentRemark.objects.filter(student=selected_child, term=selected_term).first()

        class_score = sum(float(r.calc_ca or 0) for r in results)
        exam_score = sum(float(r.calc_exam or 0) for r in results)
        total_score = class_score + exam_score
        n = len(results) or 1
        percentage = round(total_score / n, 1)

        term_name = getattr(selected_term, 'name', str(selected_term))
        academic_year_obj = getattr(selected_term, 'academic_year', None)
        summary_qs = StudentTermSummary.objects.filter(student=selected_child, term=term_name)
        if academic_year_obj:
            summary_qs = summary_qs.filter(academic_year=academic_year_obj)
        summary = summary_qs.first()

        position = 1
        class_size = 1
        overall_grade = 'F'
        overall_remark = 'Fail'

        if summary:
            if summary.term_total: total_score = float(summary.term_total)
            if summary.term_average: percentage = float(summary.term_average)
            if summary.term_grade: overall_grade = summary.term_grade
            if summary.term_remark: overall_remark = summary.term_remark
            if summary.rank: position = summary.rank
            if summary.school_class_id:
                class_size = StudentTermSummary.objects.filter(
                    school_class=summary.school_class,
                    term=summary.term,
                    academic_year=summary.academic_year
                ).count() or 1
        else:
            if percentage >= 80: overall_grade, overall_remark = 'A', 'Excellent'
            elif percentage >= 70: overall_grade, overall_remark = 'B', 'Very Good'
            elif percentage >= 60: overall_grade, overall_remark = 'C', 'Good'
            elif percentage >= 50: overall_grade, overall_remark = 'D', 'Pass'
            else: overall_grade, overall_remark = 'F', 'Fail'

        term_data = {
            'term': selected_term,
            'results': results,
            'grand_class_score': class_score,
            'grand_exam_score': exam_score,
            'grand_total_score': round(total_score,1),
            'grand_percentage': percentage,
            'overall_grade': overall_grade,
            'overall_remark': overall_remark,
            'class_position': position,
            'class_size': class_size,
            'summary': summary,
        }

        start = selected_term.start_date
        today = timezone.now().date()
        end = selected_term.end_date if selected_term.end_date < today else today

        holiday_dates = set()
        if school:
            events = AcademicCalendar.objects.filter(school=school, start_date__lte=end, end_date__gte=start, affects_timetable=True)
            for event in events:
                d = event.start_date
                while d <= event.end_date:
                    if start <= d <= end:
                        holiday_dates.add(d)
                    d += timedelta(days=1)

        attendance_records = AttendanceRecord.objects.filter(
            student=selected_child,
            session__date__gte=start,
            session__date__lte=end
        ).exclude(session__date__in=holiday_dates).values('session__date', 'status')

        by_date = defaultdict(list)
        for rec in attendance_records:
            by_date[rec['session__date']].append(rec['status'])

        times_present = 0
        times_absent = 0
        for statuses in by_date.values():
            if 'P' in statuses:
                times_present += 1
            else:
                times_absent += 1

        times_late = 0
        times_excused = 0
        days_opened = sum(1 for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5 and (start+timedelta(days=i)) not in holiday_dates)
        attendance_percentage = round((times_present / days_opened * 100), 1) if days_opened else 0

        term_data.update({
            'days_opened': days_opened,
            'times_present': times_present,
            'times_absent': times_absent,
            'times_late': times_late,
            'times_excused': times_excused,
            'attendance_percentage': attendance_percentage,
        })

    context = {
        'children': children,
        'children_count': children_count,
        'selected_child': selected_child,
        'terms': all_terms,
        'years': years,
        'selected_term': selected_term,
        'selected_year': selected_year_id or "",
        'term_data': term_data,
        'summary': summary,
        'school': school,
        'remark': remark,
    }
    return render(request, 'accounts/view_child_results.html', context)


@login_required
def view_child_fees(request):
    if request.user.role!= 'parent':
        return redirect('accounts:dashboard')

    links = ParentStudentLink.objects.filter(parent=request.user).select_related('student')
    children = [link.student for link in links]

    selected_child = None
    if len(children) == 1:
        selected_child = children[0]
    else:
        child_id = request.GET.get('child') or request.session.get('selected_child_id')
        if child_id:
            selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
        if not selected_child and children:
            selected_child = children[0]

    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    student_fees = []
    totals = {}
    school = request.user.school
    latest_fee = None
    all_payment_options = []
    transactions = []

    FEE_ITEM_FIELDS = [
        ('school_fees', 'amount_paid_school_fees', 'School Fees'),
        ('pta_dues', 'amount_paid_pta_dues', 'PTA Dues'),
        ('boarding_fee', 'amount_paid_boarding_fee', 'Boarding Fee'),
        ('hostel_fee', 'amount_paid_hostel_fee', 'Hostel Fee'),
        ('development_fee', 'amount_paid_development_fee', 'Development Fee'),
        ('computer_levy', 'amount_paid_computer_levy', 'Computer Levy'),
        ('exam_fees', 'amount_paid_exam_fees', 'Exam Fees'),
        ('canteen_amount', 'amount_paid_canteen', 'Feeding Fee'),
        ('other_fees', 'amount_paid_other_fees', 'Other Fees'),
    ]

    if selected_child:
        
        transactions = PaymentTransaction.objects.filter(
            student=selected_child,
            student__school=selected_child.school
        ).prefetch_related('items').order_by('-created_at')

        student_fees = StudentFee.objects.filter(
            student=selected_child
        ).select_related('term').order_by('-academic_year', '-id')
        
        for fee in student_fees:
            try:
                ay_obj = AcademicYear.objects.get(name=fee.academic_year)
            except AcademicYear.DoesNotExist:
                ay_obj = None
            
            paid_sum = 0
            if ay_obj:
                paid_sum = PaymentTransaction.objects.filter(
                    student=fee.student,
                    term=fee.term,
                    academic_year=ay_obj
                ).aggregate(total=Sum('total_amount'))['total'] or 0

            real_paid = float(paid_sum)
            real_balance = float(fee.total_amount) - real_paid
            if real_balance < 0:
                real_balance = 0

            fee.amount_paid_val = real_paid
            fee.balance_val = real_balance
            
# Calculate real paid amount per fee type from PaymentItem - for ALL fees
            paid_by_type = PaymentItem.objects.filter(
                transaction__student=fee.student,
                transaction__term=fee.term,
                transaction__academic_year__name=fee.academic_year
            ).values('fee_name').annotate(total=Sum('amount'))

            paid_map = {p['fee_name']: float(p['total']) for p in paid_by_type}

            fee.breakdown_items = []
            for due_field, paid_field, label in FEE_ITEM_FIELDS:
                amount_due = getattr(fee, due_field, 0) or 0
                amount_paid_field = paid_map.get(label, 0)  # ← Use PaymentItem for ALL fees
                
                if amount_due > 0:
                    balance = float(amount_due) - float(amount_paid_field)
                    fee.breakdown_items.append({
                        'label': label,
                        'value': amount_due,
                        'paid': amount_paid_field,
                        'balance': balance,
                    })


        for fee in student_fees:
            for due_field, paid_field, label in FEE_ITEM_FIELDS:
                amount_due = getattr(fee, due_field, 0) or 0
                amount_paid_field = getattr(fee, paid_field, 0) or 0  # ← DELETE
                amount_paid_field = paid_map.get(label, 0)  # ← ADD THIS
                balance = float(amount_due) - float(amount_paid_field)
                if amount_due > 0 and balance > 0.01:
                    all_payment_options.append({
                        'label': label,
                        'paid_field': paid_field,
                        'balance': balance,
                        'fee_id': fee.id,
                    })

        totals['total_amount'] = sum(f.total_amount for f in student_fees)
        totals['total_paid'] = sum(f.amount_paid_val for f in student_fees)
        totals['total_balance'] = sum(f.balance_val for f in student_fees)

        latest_fee = student_fees.first() if student_fees else None

    context = {
        'children': children,
        'selected_child': selected_child,
        'student_fees': student_fees,
        'latest_fee': latest_fee,
        'all_payment_options': all_payment_options,
        'totals': totals,
        'transactions': transactions,
        'school': school,
        'show_switcher': len(children) > 1,
    }
    return render(request, 'accounts/view_child_fees.html', context)

@login_required
def view_child_progress(request):
    if request.user.role!= "parent":
        return redirect('accounts:dashboard')

    def grade_from_pct(p):
        if p >= 80: return 'A'
        if p >= 70: return 'B'
        if p >= 60: return 'C'
        if p >= 50: return 'D'
        if p >= 40: return 'E'
        return 'F'

    def remark_from_grade(g):
        return {'A':'Excellent','B':'Very Good','C':'Good','D':'Credit','E':'Pass','F':'Fail'}.get(g,'')

    links = ParentStudentLink.objects.filter(parent=request.user).select_related('student', 'student__school', 'student__school_class')
    children = [link.student for link in links]
    children_count = len(children)

    child_id = request.GET.get('child_id') or request.session.get('selected_child_id')
    selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
    if not selected_child and children:
        selected_child = children[0]
    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    selected_link = next((l for l in links if l.student_id == getattr(selected_child, 'id', None)), None)

    # === FILTER PARAMS ===
    selected_year_id = request.GET.get('year')
    selected_term_id = request.GET.get('term') or request.GET.get('term_id')

    years = []
    terms = []
    terms_data = []

    if selected_child:
        child_school = selected_child.school

        # MULTI-SCHOOL: years and terms belong to child's school
        years = AcademicYear.objects.filter(school=child_school).order_by('-name')
        terms_qs = Term.objects.filter(school=child_school).order_by('-start_date')
        if selected_year_id:
            terms_qs = terms_qs.filter(academic_year_id=selected_year_id)
        terms = terms_qs

        qs = Result.objects.filter(
            student=selected_child,
            student__school=child_school,
            status='published'
        ).select_related('subject', 'term', 'term__academic_year')

        # APPLY FILTERS
        if selected_year_id:
            qs = qs.filter(term__academic_year_id=selected_year_id)

        if selected_term_id:
            qs = qs.filter(term_id=selected_term_id, term__school=child_school)

        by_term = defaultdict(list)
        for r in qs:
            by_term[r.term].append(r)

        for term_obj, results in by_term.items():
            def ca_max(t): return float(getattr(t, 'ca_total', 0) or 0)
            def exam_max(t): return float(getattr(t, 'exam_total', 0) or 0)

            class_score = sum(float(r.class_score or 0) for r in results)
            exam_score = sum(float(r.exam_score or 0) for r in results)
            class_max = sum(ca_max(r.term) for r in results)
            exam_max_sum = sum(exam_max(r.term) for r in results)

            total_score = class_score + exam_score
            total_max = class_max + exam_max_sum
            percentage = round(total_score / total_max * 100, 2) if total_max else 0
            overall_grade = grade_from_pct(percentage)
            overall_remark = remark_from_grade(overall_grade)
            position = 1
            class_size = 1

            term_name = getattr(term_obj, 'name', str(term_obj))
            academic_year_obj = getattr(term_obj, 'academic_year', None)

            summary_qs = StudentTermSummary.objects.filter(
                student=selected_child,
                student__school=child_school,
                term=term_name
            )
            if academic_year_obj:
                summary_qs = summary_qs.filter(academic_year=academic_year_obj)

            summary = summary_qs.first()
            if summary:
                if summary.term_total:
                    total_score = float(summary.term_total)
                if summary.term_average:
                    percentage = float(summary.term_average)
                if summary.term_grade:
                    overall_grade = summary.term_grade
                if summary.term_remark:
                    overall_remark = summary.term_remark
                if summary.rank:
                    position = summary.rank
                if summary.school_class_id:
                    class_size = StudentTermSummary.objects.filter(
                        school_class=summary.school_class,
                        school_class__school=child_school,
                        term=summary.term,
                        academic_year=summary.academic_year
                    ).count() or 1

            terms_data.append({
                'term': term_obj,
                'results': results,
                'grand_class_score': class_score,
                'grand_class_max': class_max,
                'grand_exam_score': exam_score,
                'grand_exam_max': exam_max_sum,
                'grand_total_score': total_score,
                'grand_total_max': total_max,
                'grand_percentage': percentage,
                'overall_grade': overall_grade,
                'overall_remark': overall_remark,
                'class_position': position,
                'class_size': class_size,
                'term_status': 'Published',
            })

    context = {
        'children': children,
        'children_count': children_count,
        'selected_child': selected_child,
        'relationship': getattr(selected_link, 'relationship', 'Child'),
        'terms': terms_data,
        'years': years,
        'terms_list': terms,
        'selected_year': selected_year_id or "",
        'selected_term': selected_term_id or "",
        'school': getattr(selected_child, 'school', None),
    }
    return render(request, 'accounts/view_child_progress.html', context)

# ----------------------------
# MESSAGES
# ----------------------------
@login_required
def send_message(request):
    if request.user.role != 'parent':
        return redirect('accounts:dashboard')
    teachers = User.objects.filter(role='teacher')
    if request.method == 'POST':
        receiver_id = request.POST.get('receiver')
        subject = request.POST.get('subject')
        body = request.POST.get('body')
        try:
            receiver = User.objects.get(id=receiver_id, role='teacher')
            Message.objects.create(sender=request.user, receiver=receiver, subject=subject, body=body)
            messages.success(request, f"Message sent to {receiver.username} successfully!")
            return redirect('accounts:send-message')
        except User.DoesNotExist:
            messages.error(request, "Selected teacher does not exist.")
            return redirect('accounts:send-message')
    return render(request, 'accounts/send_message.html', {'teachers': teachers})

# ----------------------------
# SYSTEM SETTINGS
# ----------------------------
@login_required
def system_settings(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')
    return render(request, 'accounts/system_settings.html')


# ----------------------------
# REPORTS
# ----------------------------
@login_required
def student_performance_report(request):
    if request.user.role not in ['admin','headmaster','hod','teacher']:
        return redirect('accounts:dashboard')

    school = request.user.school
    classes = SchoolClass.objects.filter(school=school)
    terms = Term.objects.all().order_by('-start_date')

    selected_class = request.GET.get('class')
    selected_term = request.GET.get('term')
    is_print = request.GET.get('print') == '1'

    term_obj = None
    if selected_term:
        term_obj = Term.objects.filter(id=selected_term).first()

    students_qs = User.objects.filter(role='student', school_class__school=school)
    if selected_class:
        students_qs = students_qs.filter(school_class_id=selected_class)

    temp_list = []
    for student in students_qs:
        if term_obj:
            results = Result.objects.filter(student=student, term=term_obj, status='published')
        else:
            results = Result.objects.filter(student=student, status='published')

        if results.count() == 0:
            if not selected_class:
                continue # Hide no-result students from All Classes
            avg, total, grade, remark = 0, 0, '-', 'No Results'
        else:
            total = 0
            for r in results:
                try: cs = float(r.class_score or 0)
                except: cs = 0
                try: es = float(r.exam_score or 0)
                except: es = 0
                total += cs + es
            avg = round(total / results.count(), 2)
            if avg >= 80: grade, remark = 'A', 'Excellent'
            elif avg >= 70: grade, remark = 'B', 'Very Good'
            elif avg >= 60: grade, remark = 'C', 'Good'
            elif avg >= 50: grade, remark = 'D', 'Credit'
            elif avg >= 40: grade, remark = 'E', 'Pass'
            else: grade, remark = 'F', 'Fail'

        temp_list.append({
            'student': student,
            'school_class': student.school_class,
            'term_total': total,
            'term_average': avg,
            'term_grade': grade,
            'term_remark': remark,
        })

    # ---- NEW GROUPING LOGIC FOR ALL CLASSES ----
    grouped_summaries = None
    summaries = []

    if not selected_class: # ALL CLASSES MODE
        grouped = defaultdict(list)
        for item in temp_list:
            grouped[item['school_class']].append(item)

        grouped_summaries = []
        for school_class, items in sorted(grouped.items(), key=lambda x: x[0].name):
            # Sort inside class by average
            sorted_items = sorted(items, key=lambda x: x['term_average'], reverse=True)
            class_summaries = []
            for i, item in enumerate(sorted_items):
                item['rank'] = i + 1
                class Dummy: pass
                d = Dummy()
                d.rank, d.student, d.school_class = item['rank'], item['student'], item['school_class']
                d.term_total, d.term_average, d.term_grade, d.term_remark = item['term_total'], item['term_average'], item['term_grade'], item['term_remark']
                class_summaries.append(d)

            class_avg = round(sum(s.term_average for s in class_summaries) / len(class_summaries), 1) if class_summaries else 0
            grouped_summaries.append({
                'class_obj': school_class,
                'students': class_summaries,
                'total_students': len(class_summaries),
                'class_avg': class_avg,
            })
            summaries.extend(class_summaries) # For overall stats

        # Sort summaries for overall top student
        summaries = sorted(summaries, key=lambda x: x.term_average, reverse=True)
    else: # SINGLE CLASS MODE (old logic)
        temp_list = sorted(temp_list, key=lambda x: x['term_average'], reverse=True)
        for i, item in enumerate(temp_list):
            item['rank'] = i+1
            class Dummy: pass
            d = Dummy()
            d.rank, d.student, d.school_class = item['rank'], item['student'], item['school_class']
            d.term_total, d.term_average, d.term_grade, d.term_remark = item['term_total'], item['term_average'], item['term_grade'], item['term_remark']
            summaries.append(d)

    total_students = len(summaries)
    avg_score = round(sum([s.term_average for s in summaries]) / total_students, 1) if total_students else 0
    passed = len([s for s in summaries if s.term_average >= 50])
    pass_rate = round(passed / total_students * 100, 1) if total_students else 0
    top_student = summaries[0] if summaries else None

    # PRINT MODE
    if is_print:
        class_name = SchoolClass.objects.filter(id=selected_class).first().name if selected_class else "All Classes"
        term_name = str(term_obj) if term_obj else "All Terms"
        return render(request, 'accounts/student_performance_print.html', {
            'summaries': summaries,
            'grouped_summaries': grouped_summaries,
            'total_students': total_students,
            'class_name': class_name, 'term_name': term_name,
            'school': school, 'avg_score': avg_score, 'pass_rate': pass_rate,
        })

    # NORMAL MODE
    if not selected_class:
        # For grouped view, no pagination - show all class cards
        return render(request, 'accounts/student_performance_report.html', {
            'classes': classes, 'terms': terms,
            'selected_class': selected_class, 'selected_term': selected_term,
            'grouped_summaries': grouped_summaries, # <-- NEW
            'summaries': summaries,
            'total_students': total_students, 'avg_score': avg_score,
            'pass_rate': pass_rate, 'top_student': top_student,
        })
    else:
        paginator = Paginator(summaries, 10)
        page_obj = paginator.get_page(request.GET.get('page'))
        return render(request, 'accounts/student_performance_report.html', {
            'classes': classes, 'terms': terms,
            'selected_class': selected_class, 'selected_term': selected_term,
            'summaries': page_obj,
            'grouped_summaries': None,
            'total_students': total_students, 'avg_score': avg_score,
            'pass_rate': pass_rate, 'top_student': top_student,
        })


@login_required
def student_attendance_report(request):
    if request.user.role not in ['admin','headmaster','hod','teacher']:
        return redirect('accounts:home')

    school = request.user.school
    classes = SchoolClass.objects.filter(school=school)
    selected_class = request.GET.get('class')
# Fix None string bug
    if not selected_class or selected_class == 'None' or selected_class == 'null':
        selected_class = None
    is_print = request.GET.get('print') == '1'

    sessions = AttendanceSession.objects.filter(
        school_class__school=school
    ).select_related('school_class','subject','teacher').prefetch_related('records__student').order_by('-date')

    if selected_class:
        try:
            sessions = sessions.filter(school_class_id=int(selected_class))
        except:
            selected_class = None

    # MERGE per (date, class) - YOUR LOGIC: If ANY P that day = Present
    daily_map = defaultdict(list)
    for s in sessions:
            # Use only date part, ignore time
            only_date = s.date.date() if hasattr(s.date, 'date') else s.date
            daily_map[(only_date, s.school_class_id)].append(s)

    merged_days = []
    for (date, class_id), sess_list in sorted(daily_map.items()): # Day 1 to last day
        school_class = sess_list[0].school_class

        student_status = {}
        has_any_record = False # <-- NEW
        for sess in sess_list:
            records = list(sess.records.all())
            if records: # if this session has at least 1 student marked
                has_any_record = True
            for rec in records:
                student_status.setdefault(rec.student.id, []).append(rec.status)

        # SKIP if no teacher marked attendance that day
        if not has_any_record:
            continue
        if not student_status: # also skip if still empty
            continue

        present_day = 0
        absent_day = 0
        for statuses in student_status.values():
            if 'P' in statuses:
                present_day += 1
            else:
                absent_day += 1

        merged_days.append({
            'date': date,
            'school_class': school_class,
            'present_count': present_day,
            'absent_count': absent_day,
            'total': present_day + absent_day,
        })


# TERM TOTAL - Sum ALL days (NOT inside loop!)
    total_present = sum(d['present_count'] for d in merged_days)
    total_absent = sum(d['absent_count'] for d in merged_days)
    total_all = total_present + total_absent
    attendance_rate = round(total_present/total_all*100,1) if total_all else 0

    selected_obj = None
    if selected_class:
        selected_obj = SchoolClass.objects.filter(id=selected_class).first()

    # GROUP BY CLASS
    grouped_dict = defaultdict(list)
    for day in merged_days:
        grouped_dict[day['school_class']].append(day)

    grouped_list = []
    for klass, days in grouped_dict.items():
        days_sorted = sorted(days, key=lambda x: x['date'])
        grouped_list.append({
            'klass': klass,
            'days': days_sorted,
            'total_days': len(days_sorted)
        })
    grouped_list = sorted(grouped_list, key=lambda x: x['klass'].name)

    # *** PRINT CHECK - ADD THIS ***
    if is_print:
        return render(request, 'accounts/student_attendance_print.html', {
            'school': school,
            'merged_days': merged_days,
            'total_present': total_present,
            'total_absent': total_absent,
            'attendance_rate': attendance_rate,
            'grouped_by_class': grouped_list,
            'selected_class': selected_class,
            'selected_class_obj': selected_obj,
            'term': request.GET.get('term', 'All Term'),
        })

    paginator = Paginator(merged_days, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/student_attendance_report.html', {
        'school': school,
        'sessions': page_obj,
        'classes': classes,
        'selected_class': selected_class,
        'total_present': total_present,
        'total_absent': total_absent,
        'attendance_rate': attendance_rate,
        'grouped_by_class': grouped_list,
        'selected_class_obj': selected_obj,
    })


@login_required
def teacher_attendance_report(request):

    # ONLY Admin and Headmaster
    if request.user.role not in ['admin', 'headmaster']:
        messages.error(
            request,
            "You do not have permission to view teacher attendance."
        )
        return redirect('accounts:home')


    school = request.user.school

    if not school:
        messages.error(
            request,
            "Your account is not assigned to any school."
        )
        return redirect('accounts:home')


    # =========================
    # WEEK SELECTION
    # =========================

    week_str = request.GET.get('attendance_week')


    if week_str:
        selected_date = parse_date(week_str)
    else:
        selected_date = timezone.localdate()


    if not selected_date:
        selected_date = timezone.localdate()


    # If weekend, move back to Friday
    if selected_date.weekday() >= 5:
        selected_date = selected_date - timedelta(
            days=selected_date.weekday() - 4
        )


    # Find Monday of selected week
    week_start = selected_date - timedelta(
        days=selected_date.weekday()
    )

    week_end = week_start + timedelta(days=4)
    # =========================
    # ACADEMIC WEEK NUMBER
    # =========================

    active_term = Term.objects.filter(
        school=school,
        is_active=True
    ).first()

    attendance_week = None

    if active_term:

        term_start = active_term.start_date

        attendance_week = 1

        current_day = term_start

        while current_day < week_start:

            # Friday completes a school week
            if current_day.weekday() == 4:
                attendance_week += 1

            current_day += timedelta(days=1)


    # === LIMIT: Don't go beyond school term ===
    term_start = active_term.start_date if active_term else week_start
    term_end = active_term.end_date if active_term else week_end

    previous_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    is_first_week = week_start <= term_start
    is_last_week = week_end >= term_end

    if is_first_week:
        previous_week = week_start

    if is_last_week:
        next_week = week_start


    # =========================
    # WORKING DAYS
    # =========================

    working_days = []

    current = week_start

    while current <= week_end:

        if current.weekday() < 5:
            working_days.append(current)

        current += timedelta(days=1)



    # =========================
    # TEACHERS
    # =========================

    teachers = User.objects.filter(
        school=school,
        role='teacher'
    ).order_by(
        'first_name',
        'last_name'
    )



    # =========================
    # ATTENDANCE RECORDS
    # =========================

    attendance_records = TeacherAttendance.objects.filter(
        school=school,
        date__range=[
            week_start,
            week_end
        ]
    ).select_related(
        'teacher'
    )



    # Arrange attendance by teacher and date

    attendance_map = defaultdict(dict)

    for record in attendance_records:

        attendance_map[
            record.teacher_id
        ][
            record.date
        ] = record



    # =========================
    # BUILD REPORT
    # =========================

    report = []


    for teacher in teachers:

        days = []

        present = 0
        absent = 0
        late = 0
        sick = 0
        excused = 0
        leave = 0


        for day in working_days:

            attendance = attendance_map.get(
                teacher.id,
                {}
            ).get(day)


            status = None


            if attendance:

                status = attendance.status

                if status == "P":
                    present += 1

                elif status == "A":
                    absent += 1

                elif status == "L":
                    late += 1
                elif status == "E":
                    excused += 1
                elif status == "S":
                    sick += 1
                elif status == "LV":
                    leave += 1


            days.append({
                "date": day,
                "attendance": attendance,
                "status": status,
            })


        total_days = len(working_days)


        attendance_rate = 0

        if total_days:
            attendance_rate = round(
                ((present + late) / total_days) * 100,
                1
            )


        report.append({
            "teacher": teacher,
            "days": days,
            "present": present,
            "absent": absent,
            "late": late,
            "sick": sick,
            "leave": leave,
            "excused": excused,
            "attendance_rate": attendance_rate,
        })



    # =========================
    # SUMMARY CARDS
    # =========================

    total_teachers = teachers.count()


    total_present = TeacherAttendance.objects.filter(
        school=school,
        date__range=[week_start, week_end],
        status="P"
    ).count()


    total_absent = TeacherAttendance.objects.filter(
        school=school,
        date__range=[week_start, week_end],
        status="A"
    ).count()


    total_late = TeacherAttendance.objects.filter(
        school=school,
        date__range=[week_start, week_end],
        status="L"
    ).count()

    total_sick = TeacherAttendance.objects.filter(school=school, date__range=[week_start, week_end], status="S").count()
    total_excused = TeacherAttendance.objects.filter(school=school, date__range=[week_start, week_end], status="E").count()
    total_leave = TeacherAttendance.objects.filter(school=school, date__range=[week_start, week_end], status="LV").count()



    return render(
            request,
            "accounts/teacher_attendance_report.html",
            {
                "school": school,
                "report": report,
                "working_days": working_days,
                "report_week_start": week_start,
                "report_week_end": week_end,
                "report_previous_week": previous_week,
                "report_next_week": next_week,
                "is_first_week": is_first_week,      # <-- ADD THIS
                "is_last_week": is_last_week,
                "total_teachers": total_teachers,
                "total_present": total_present,
                "total_absent": total_absent,
                "total_late": total_late,
                "total_excused": total_excused,
                "total_sick": total_sick,
                "total_leave": total_leave,
                "attendance_week": attendance_week,

            }
        )


@login_required
def view_reports(request):
    if request.user.role not in ['admin', 'headmaster', 'hod', 'teacher']:
        return redirect('accounts:dashboard')
    
    school = request.user.school
    
    context = {
        'total_students': User.objects.filter(school=school, role='student').count(),
        'total_teachers': User.objects.filter(school=school, role='teacher').count(),
        'total_classes': SchoolClass.objects.filter(school=school).count(),
        'performance_reports': StudentTermSummary.objects.filter(school_class__school=school).count(),
        'attendance_sessions': AttendanceSession.objects.filter(school_class__school=school).count(),
        'last_performance': StudentTermSummary.objects.filter(school_class__school=school).order_by('-date_created').first(),
        'last_attendance': AttendanceSession.objects.filter(school_class__school=school).order_by('-date').first(),
    }
    return render(request, 'accounts/view_reports.html', context)
@login_required
def assign_student_class(request, student_id):

    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    # get student
    student = get_object_or_404(User, id=student_id, role='student')

    # get all classes
    classes = SchoolClass.objects.all()

    if request.method == 'POST':
        class_id = request.POST.get('class_id')

        if class_id:
            selected_class = get_object_or_404(SchoolClass, id=class_id)

            student.school_class = selected_class
            student.save()

            messages.success(request, "Student assigned to class successfully.")
            return redirect('accounts:manage-students')

        else:
            messages.error(request, "Please select a class.")

    return render(request, 'accounts/assign_student_class.html', {
        'student': student,
        'classes': classes
    })


@login_required
def export_students_excel(request):
    if request.user.role != 'admin':

        return redirect('accounts:dashboard')

    # Create workbook and worksheet
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Students"

    # Header row
    ws.append([
        "First Name", "Last Name", "Username", "Email",
        "Phone", "Student Number", "Class", "Subjects", "Form Number"
    ])

    # Get all students
    students = User.objects.filter(role='student')

    for student in students:
        subjects = ", ".join([ssc.subject.name for ssc in student.studentsubjectclass_set.all()])
        ws.append([
            student.first_name,
            student.last_name,
            student.username,
            student.email,
            student.phone,
            student.student_number,
            student.school_class.name if student.school_class else "Not assigned",
            subjects if subjects else "Not assigned",
            student.form_number if student.form_number else "-"
        ])

    # Prepare response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=students.xlsx'
    wb.save(response)
    return response


# EDIT CLASS
def edit_class(request, pk):
    class_obj = SchoolClass.objects.get(id=pk)
    if request.method == "POST":
        name = request.POST.get("name")
        class_obj.name = name
        # SAVE CHECKBOX VALUE
        class_obj.allows_student_login = 'allows_student_login' in request.POST
        class_obj.save()
        messages.success(request, "Class updated successfully ✅")
        return redirect('accounts:admin_classes')
    return render(request, 'accounts/edit_class.html', {'class': class_obj})

# DELETE CLASS
def delete_class(request, pk):
    class_obj = SchoolClass.objects.get(id=pk)
    if class_obj.students.exists():
        messages.error(request, "Cannot delete class. It has students ❌")
        return redirect('accounts:admin_classes')
    class_obj.delete()
    messages.success(request, "Class deleted successfully 🗑️")
    return redirect('accounts:admin_classes')

# CLASS DETAILS
def class_detail(request, pk):
    class_obj = SchoolClass.objects.get(pk=pk)
    students = class_obj.students.all()  
    subjects = class_obj.subjects.all() 
    assignments = TeacherSubjectClass.objects.all()

    print(assignments)
    return render(request, 'accounts/class_detail.html', {
        'class_obj': class_obj,
        'students': students,
        'subjects': subjects,
        'assignments': assignments
    })

# ASING_TEACHER_SUBJECT
@login_required
def assign_teacher_subject(request):
    teachers = User.objects.filter(role='teacher')
    subjects = Subject.objects.all()
    classes = SchoolClass.objects.all()

    selected_class_id = request.GET.get("class")
    selected_class_obj = None

    if selected_class_id:
        selected_class_id = int(selected_class_id)
        selected_class_obj = SchoolClass.objects.get(id=selected_class_id)

    if request.method == "POST":
        teacher_id = request.POST.get("teacher")
        subject_id = request.POST.get("subject")
        class_id = request.POST.get("class")

        exists = TeacherSubjectClass.objects.filter(
            school_class_id=class_id,
            subject_id=subject_id
        ).exists()

        if exists:
            messages.warning(request, "This subject is already assigned in this class ⚠️")
            return redirect(f"/assign-teacher-subject/?class={class_id}")

        TeacherSubjectClass.objects.create(
            teacher_id=teacher_id,
            subject_id=subject_id,
            school_class_id=class_id
        )

        messages.success(request, "Assignment successful ✅")
        return redirect('accounts:class_detail', pk=class_id)

    return render(request, 'accounts/assign_teacher_subject.html', {
        'teachers': teachers,
        'subjects': subjects,
        'classes': classes,
        'selected_class_id': selected_class_id,
        'selected_class_obj': selected_class_obj,
    })


# -------------------------------

# ----------------------------------
def admin_classes(request):

    school = request.user.school

    classes = SchoolClass.objects.filter(
        school=school
    )

    order = {
        'creche': 1,
        'nursery': 2,
        'kg': 3,
        'lower_primary': 4,
        'upper_primary': 5,
        'jhs': 6,
        'shs': 7
    }

    STAGE_LABELS = {
        'creche': 'Creche',
        'nursery': 'Nursery',
        'kg': 'KG',
        'lower_primary': 'Lower Primary',
        'upper_primary': 'Upper Primary',
        'jhs': 'JHS',
        'shs': 'SHS'
    }

    grouped = defaultdict(dict)

    for c in classes:

        if not c.id:
            continue

        c.student_count = c.students.count()
        c.stage_label = STAGE_LABELS.get(c.stage, c.stage)

        name = c.name.strip()

        m = re.match(r"^(.*?)\s+([A-Z])$", name)

        if m:
            base_class = m.group(1).strip()
            section = m.group(2)
        else:
            base_class = name
            section = None

        c.base_class = base_class
        c.section = section

        grouped[c.stage_label].setdefault(
            base_class,
            {"classes": []}
        )

        grouped[c.stage_label][base_class]["classes"].append(c)


    grouped = dict(
        sorted(
            grouped.items(),
            key=lambda x: order.get(x[0].lower(), 999)
        )
    )


    return render(
        request,
        'accounts/admin_classes.html',
        {
            'grouped': grouped,
            'school': school,
        }
    )
# ------------------------------
# ADD SUBJECT
# ----------------------------------
@login_required
def add_subject_to_class(request, class_id):
    school_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
    
    if request.method == "POST":
        form = AddSubjectToClassForm(request.POST, school=request.user.school)
        if form.is_valid():
            selected_subjects = form.cleaned_data['subjects']
            # Add subjects to the class M2M
            school_class.subjects.add(*selected_subjects)
            
                        # Now auto-enroll all students in this class for these new subjects
            students = User.objects.filter(
                role='student',
                school=request.user.school,
                school_class=school_class
            )

            for student in students:
                for subject in selected_subjects:
                    StudentSubjectClass.objects.get_or_create(
                        student=student,
                        subject=subject,
                        school_class=school_class
                    )
            
            messages.success(request, f"Assigned {selected_subjects.count()} subjects to {school_class.name}. All students enrolled.")
            return redirect("accounts:class_detail", pk=class_id)
    else:
        form = AddSubjectToClassForm(school=request.user.school)
        # Uncheck subjects already assigned
        form.fields['subjects'].initial = school_class.subjects.all()
    
    return render(request, 'accounts/add_subject_to_class.html', {
        'form': form,
        'school_class': school_class
    })
# ---------------------------------
# VIEW SBJECTS
# --------------------------------
@login_required
def subject_list(request):
    school = request.user.school

    subjects = Subject.objects.filter(
        school=school
    ).order_by('name')

    return render(request, 'accounts/subject_list.html', {
        'subjects': subjects,
        'school': school
    })


@login_required
def edit_subject(request, pk):
    school = request.user.school

    subject = get_object_or_404(
        Subject,
        id=pk,
        school=school
    )

    if request.method == "POST":
        subject.name = request.POST.get("name")
        subject.code = request.POST.get("code")
        subject.save()

        messages.success(request, "Subject updated ✅")
        return redirect('accounts:subject_list')

    return render(request, 'accounts/edit_subject.html', {
        'subject': subject,
        'school': school
    })


@login_required
def delete_subject(request, pk):
    school = request.user.school

    subject = get_object_or_404(
        Subject,
        id=pk,
        school=school
    )

    subject.delete()

    messages.success(request, "Subject deleted ✅")

    return redirect('accounts:subject_list')


# ---------------------------------

# --------------------------------
@login_required
def export_teachers_excel(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')
    teachers = User.objects.filter(role='teacher').distinct()

    # Create workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Teachers"

    # Header row
    ws.append(["First Name", "Last Name", "Username", "Email", "Phone", "School", "Classes", "Subjects"])

    # Data rows
    for teacher in teachers:
        classes_list = []
        subjects_list = []

        for tc in teacher.teachersubjectclass_set.all():
            if tc.school_class:
                classes_list.append(tc.school_class.name)
            if tc.subject:
                subjects_list.append(tc.subject.name)

        classes = ", ".join(classes_list)
        subjects = ", ".join(subjects_list)
        
        
        ws.append([
            teacher.first_name,
            teacher.last_name,
            teacher.username,
            teacher.email,
            teacher.phone or "Not provided",
            teacher.school.name if teacher.school else "No school assigned",
            classes or "Not assigned",
            subjects or "Not assigned"
        ])

    # Prepare response
    response = HttpResponse(content_type="application/ms-excel")
    response['Content-Disposition'] = 'attachment; filename=teachers.xlsx'
    wb.save(response)
    return response

# Edit_ASSIGNMENT
@login_required
def edit_assignment(request, id):
    assignment = get_object_or_404(
        TeacherSubjectClass,
        id=id,
        teacher__school=request.user.school
    )

    school = request.user.school

    classes = SchoolClass.objects.filter(school=school).order_by("name")
    subjects = Subject.objects.filter(school=school).order_by("name")

    if request.method == 'POST':
        new_class = request.POST.get('school_class')
        new_subject = request.POST.get('subject')

        if (
            str(assignment.school_class_id) == new_class and
            str(assignment.subject_id) == new_subject
        ):
            return redirect('accounts:assigned_teachers_list')

        exists = TeacherSubjectClass.objects.filter(
            teacher=assignment.teacher,
            school_class_id=new_class,
            subject_id=new_subject
        ).exclude(id=assignment.id).exists()

        if exists:
            return render(request, 'accounts/edit_assignment.html', {
                'assignment': assignment,
                'classes': classes,
                'subjects': subjects,
                'error': 'This teacher already has this class + subject assigned!'
            })

        assignment.school_class_id = new_class
        assignment.subject_id = new_subject
        assignment.save()

        messages.success(request, "Teacher assignment updated successfully.")
        return redirect('accounts:assigned_teachers_list')

    return render(request, 'accounts/edit_assignment.html', {
        'assignment': assignment,
        'classes': classes,
        'subjects': subjects,
    })


@login_required
def delete_assignment(request, id):
    assignment = get_object_or_404(
        TeacherSubjectClass, 
        id=id, 
        teacher__school=request.user.school  # <-- USE THIS, not school=
    )
    
    if request.method != "POST":
        return redirect('accounts:view-teacher', teacher_id=assignment.teacher.id)
    
    teacher_id = assignment.teacher.id
    subject_id = assignment.subject.id
    class_id = assignment.school_class.id

    subject_name = assignment.subject.name
    teacher_name = assignment.teacher.get_full_name() or assignment.teacher.username
    class_name = assignment.school_class.name
    
    assignment.delete()
    
    Timetable.objects.filter(
        teacher_id=teacher_id,
        subject_id=subject_id,
        school_class_id=class_id
    ).delete()
    
    messages.success(request, f'Removed {teacher_name} from {subject_name} in {class_name}.')
    return redirect('accounts:view-teacher', teacher_id=teacher_id)

@login_required
def add_assignment(request):
    if request.method == 'POST':
        teacher_id = request.POST.get('teacher')
        class_id = request.POST.get('school_class')
        subject_id = request.POST.get('subject')

        # Check for duplicate
        exists = TeacherSubjectClass.objects.filter(
            school_class_id=class_id,
            subject_id=subject_id
        ).exists()

        if exists:
            messages.warning(
                request,
                "This subject is already assigned to a teacher in this class ⚠️"
            )
            return redirect(f"/assign-teacher-subject/?class={class_id}")

        TeacherSubjectClass.objects.create(
            teacher_id=teacher_id,
            subject_id=subject_id,
            school_class_id=class_id
        )

        messages.success(request, "Assignment added successfully!")
        return redirect('accounts:add_assignment')

    return render(request, 'accounts/add_assignment.html')

def test_view(request):
    return HttpResponse("Test page works")


@login_required
def remove_student_subject(request, student_id, subject_id):
    student = get_object_or_404(
        User,
        id=student_id,
        role='student',
        school=request.user.school
    )

    subject = get_object_or_404(
        Subject,
        id=subject_id,
        school=request.user.school
    )

    StudentSubjectClass.objects.filter(
        student=student,
        subject=subject,
        school_class=student.school_class
    ).delete()

    messages.success(
        request,
        f'{subject.name} removed from {student.first_name}'
    )

    return redirect(
        'accounts:view-student',
        student_id=student.id
    )


@login_required
def add_subjects_to_class(request):
    if request.method == "POST":
        class_id = request.POST.get("class_id")
        subject_ids = request.POST.getlist("subjects")
        
        if class_id and subject_ids:
            school_class = SchoolClass.objects.get(id=class_id)
            students = User.objects.filter(role='student', school_class=school_class)
            # Replace 'student_class' with your actual field name that links User to SchoolClass
            
            for student in students:
                for subject_id in subject_ids:
                    StudentSubjectClass.objects.get_or_create(
                        student=student,
                        subject_id=subject_id,
                        school_class=school_class
                    )
                messages.success(request, f"Subjects added to all students in {school_class.name} ✅")
            return redirect(request.META.get('HTTP_REFERER', '/'))
    
    return redirect(request.META.get('HTTP_REFERER', '/'))


@login_required
def parent_dashboard(request):
    if request.user.role!= 'parent':
        return redirect('accounts:dashboard')

    links = ParentStudentLink.objects.filter(parent=request.user).select_related('student', 'student__school_class', 'student__school')
    children = [link.student for link in links]

    child_id = request.GET.get('child_id') or request.session.get('selected_child_id')
    selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
    if not selected_child and children:
        selected_child = children[0]

    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    active_term = Term.objects.filter(is_active=True).first()
    school = request.user.school

    announcement_count = 0
    fees_status = {'text': 'No data', 'color': 'secondary', 'amount': 0, 'balance': 0}
    academic_status = {'text': 'No data', 'color': 'secondary', 'score': 0, 'grade': '-'}
    attendance_pct = 0
    attendance_text = "0%"
    present_days = 0
    spent_days = 0
    total_days = 0
    attendance_display = "0/0"

    if children and active_term and selected_child:
        # 1. ANNOUNCEMENTS
        today = timezone.now().date()
        all_anns = Announcement.objects.filter(
                    school=school,
                    created_at__date__gte=active_term.start_date,
                    created_at__date__lte=today,
                    is_active=True
                ).order_by('-created_at')

        announcements_list = []
        for ann in all_anns:
            roles = ann.target_roles or []
            if isinstance(roles, str):
                import ast
                try:
                    roles = ast.literal_eval(roles)
                except:
                    roles = [r.strip(" '[]") for r in roles.split(",")]
            if any(r in roles for r in ['all', 'parents', 'students']):
                if ann.created_at.weekday() < 5:
                    announcements_list.append(ann)

        announcements = announcements_list
        announcement_count = len(announcements)

        # 2. FEES
        fee_record = StudentFee.objects.filter(
            student=selected_child,
            term=active_term,
            academic_year=active_term.academic_year.name if hasattr(active_term.academic_year, 'name') else active_term.academic_year
        ).first()

        if fee_record:
            balance = fee_record.balance()
            if balance <= 0:
                fees_status = {'text': 'All Paid ✓', 'color': 'success', 'amount': 0, 'balance': 0}
            else:
                fees_status = {'text': f'GHS {balance}', 'color': 'danger', 'amount': balance, 'balance': balance}
        else:
            fees_status = {'text': 'No fees set', 'color': 'secondary', 'amount': 0, 'balance': 0}

        # 3. ACADEMIC
        results = Result.objects.filter(student=selected_child, term=active_term, status='published')
        percentages = [r.percentage for r in results if r.percentage is not None]
        avg_score = sum(percentages) / len(percentages) if percentages else 0

        def get_grade(pct):
            if pct >= 80: return "A"
            elif pct >= 70: return "B"
            elif pct >= 60: return "C"
            elif pct >= 50: return "D"
            elif pct >= 40: return "E"
            else: return "F"

        if avg_score >= 80:
            academic_status = {'text': 'Excellent', 'color': 'success', 'score': round(avg_score,1), 'grade': get_grade(avg_score)}
        elif avg_score >= 70:
            academic_status = {'text': 'Very Good', 'color': 'primary', 'score': round(avg_score,1), 'grade': get_grade(avg_score)}
        elif avg_score >= 60:
            academic_status = {'text': 'Good', 'color': 'info', 'score': round(avg_score,1), 'grade': get_grade(avg_score)}
        elif avg_score >= 50:
            academic_status = {'text': 'Pass', 'color': 'warning', 'score': round(avg_score,1), 'grade': get_grade(avg_score)}
        elif avg_score >= 40:
            academic_status = {'text': 'Weak', 'color': 'danger', 'score': round(avg_score,1), 'grade': get_grade(avg_score)}
        elif avg_score > 0:
            academic_status = {'text': 'Fail', 'color': 'danger', 'score': round(avg_score,1), 'grade': "F"}
        else:
            academic_status = {'text': 'No grades', 'color': 'secondary', 'score': 0, 'grade': '-'}

        # 4. ATTENDANCE - FINAL RULE: ONLY P and A, 1 P = Whole day Present
        today = timezone.now().date()
        spent_days = calculate_school_days(active_term.start_date, today, selected_child.school)
        total_days = calculate_school_days(active_term.start_date, active_term.end_date, selected_child.school)

        records = AttendanceRecord.objects.filter(
            student=selected_child,
            session__date__gte=active_term.start_date,
            session__date__lte=today
        ).values('session__date', 'status')

        by_date = defaultdict(list)
        for r in records:
            by_date[r['session__date']].append(r['status'])

        present_days = 0
        absent_days = 0

        for date, statuses in by_date.items():
            # FINAL RULE: If any P that day = Present, else Absent (No Late)
            if 'P' in statuses:
                present_days += 1
            else:
                absent_days += 1

        attendance_pct = round((present_days / spent_days * 100), 1) if spent_days else 0
        attendance_text = f"{attendance_pct}%"
        attendance_display = f"{present_days}/{spent_days}"

    context = {
        'parent': request.user,
        'children': children,
        'children_count': len(children),
        'selected_child': selected_child,
        'announcement_count': announcement_count,
        'fees_status': fees_status,
        'academic_status': academic_status,
        'attendance_pct': attendance_pct,
        'attendance_text': attendance_text,
        'active_term': active_term,
        'present_days': present_days,
        'spent_days': spent_days,
        'total_days': total_days,
        'attendance_display': attendance_display,
        }
    return render(request, 'accounts/parent_dashboard.html', context)
@login_required
def student_profile(request, student_id=None):
    if request.user.role == 'student':
        student = request.user
    else:
        student = get_object_or_404(User, id=student_id, role='student')
    if request.user.role == 'parent':
        if not ParentStudentLink.objects.filter(
            parent=request.user,
            student=student
        ).exists():
            return redirect('accounts:parent_dashboard')

    active_term = Term.objects.filter(
        school=student.school,
        is_active=True
    ).first()

    context = {
        'student': student,
        'active_term': active_term,
    }

    return render(request, 'accounts/student_profile.html', context)


@login_required
@role_required(['student'])
def download_results_pdf(request):
    student = request.user
    school=student.school,
    
    active_term = Term.objects.filter(is_active=True, school=school).first()
    
    if not active_term:
        return HttpResponse("No active term set. Contact your admin.", status=400)
    
    term = int(request.GET.get('term', active_term.term_number))
    year = request.GET.get('year', active_term.academic_year)
    
    results = Result.objects.filter(
        student=student,
        school=school,
        term=term,
        academic_year=year,
        status='published'  
    ).select_related('subject')

    totals = results.aggregate(
        total_score=Sum('score'),
        subject_count=Count('id')
    )
    total_score = totals['total_score'] or 0
    subject_count = totals['subject_count'] or 0
    max_total = subject_count * 100
    average_score = round(total_score / subject_count, 1) if subject_count > 0 else 0.0

    # 3. GRADE
    if average_score >= 80: overall_grade = "A"
    elif average_score >= 70: overall_grade = "B"
    elif average_score >= 60: overall_grade = "C"
    elif average_score >= 50: overall_grade = "D"
    elif average_score >= 40: overall_grade = "E"
    else: overall_grade = "F"

    # 4. POSITION
    try:
        term_summary = StudentTermSummary.objects.get(
            student=student,
            school=school,
            term=f"Term {term}",
            academic_year=year
        )
        position_in_class = term_summary.rank
    except StudentTermSummary.DoesNotExist:
        position_in_class = '-'

    total_students_in_class = student.school_class.students.count() if student.school_class else 0

    # 5. ATTENDANCE
    sessions = AttendanceSession.objects.filter(school_class=student.school_class, school=school)
    days_opened = sessions.count()
    
    days_present = AttendanceRecord.objects.filter(
        student=student,
        school=school,
        session__in=sessions,
        status='P'
    ).count()
    
    days_absent = days_opened - days_present
    attendance_percentage = round((days_present / days_opened) * 100, 1) if days_opened > 0 else 0.0

    context = {
        'student': student,
        'results': results,
        'term': term,
        'year': year,
        'active_term': active_term,  # pass this for the template banner
        'date_generated': timezone.now(),
        
        'total_score': total_score,
        'max_total': max_total,
        'average_score': average_score,
        'overall_grade': overall_grade,
        'position_in_class': position_in_class,
        'total_students_in_class': total_students_in_class,
        
        'days_opened': days_opened,
        'days_present': days_present,
        'days_absent': days_absent,
        'attendance_percentage': attendance_percentage,
        
        'class_teacher_remark': None,
        'headmaster_remark': None,
        'next_term_date': active_term.next_term_begins,
        'fees_balance': None,
    }
    
    html_string = render_to_string('accounts/results_pdf.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf = html.write_pdf()
    
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{student.username}_Term{term}_Results.pdf"'
    return response


@login_required
def student_attendance(request):
    if request.user.role!= 'student':
        messages.error(request, "Only students can view this page.")
        return redirect('accounts:student_dashboard')

    student = request.user

    # MULTI-SCHOOL FIX
    setting = SchoolSetting.objects.filter(school=student.school).first()
    attendance_mode = setting.attendance_mode if setting else 'subject'

    # FILTERS
    years = AcademicYear.objects.filter(school=student.school).order_by('-name')
    terms = Term.objects.filter(school=student.school).order_by('-start_date')

    selected_year_id = request.GET.get('year')
    selected_term_id = request.GET.get('term')

    if selected_term_id:
        terms_qs = Term.objects.filter(id=selected_term_id, school=student.school)
        active_term = terms_qs.first()
        # filter terms dropdown by year if year selected
        if selected_year_id:
            terms = terms.filter(academic_year_id=selected_year_id)
    elif selected_year_id:
        active_term = Term.objects.filter(academic_year_id=selected_year_id, school=student.school, is_active=True).first()
        if not active_term:
            active_term = Term.objects.filter(academic_year_id=selected_year_id, school=student.school).order_by('-start_date').first()
        terms = terms.filter(academic_year_id=selected_year_id)
    else:
        active_term = Term.objects.filter(school=student.school, is_active=True).first()

    week_offset = int(request.GET.get('s_week', 0))
    today = timezone.now().date()
    current_monday = today - timedelta(days=today.weekday())
    target_monday = current_monday + timedelta(weeks=week_offset)
    target_friday = target_monday + timedelta(days=4)

    # TERM WEEK HANDLING
    if active_term:
        term_start = active_term.start_date
        term_end = active_term.end_date
        week1_end = term_start + timedelta(days=(4 - term_start.weekday()))
        if target_friday < term_start:
            target_monday = term_start
            target_friday = week1_end
        elif target_monday <= term_start <= target_friday:
            target_monday = term_start
            target_friday = week1_end
        first_full_monday = term_start + timedelta(days=(7 - term_start.weekday()))
        if target_monday < first_full_monday:
            active_week = 1
        else:
            active_week = ((target_monday - first_full_monday).days // 7) + 2
        # keep week inside selected term
        if target_monday > term_end:
            target_monday = term_end - timedelta(days=4)
            target_friday = term_end
    else:
        active_week = 1

    s_prev_week = week_offset if active_week == 1 else week_offset - 1

    # ==========================
    # CLASS TEACHER MODE - ONLY P and A
    # ==========================
    if attendance_mode == 'class_teacher':
        qs = AttendanceRecord.objects.filter(
            student=student,
            school=student.school,
            session__date__gte=target_monday,
            session__date__lte=target_friday
        )
        # APPLY YEAR FILTER FOR STATS
        if selected_year_id:
            qs = qs.filter(session__term__academic_year_id=selected_year_id)

        raw = qs.values('session__date', 'status')

        by_date = defaultdict(list)
        for row in raw:
            by_date[row['session__date']].append(row['status'])

        records = []
        for date, statuses in by_date.items():
            final_status = 'P' if 'P' in statuses else 'A'
            records.append({'session__date': date, 'status': final_status})

        records = sorted(records, key=lambda x: x['session__date'], reverse=True)
        total = len(records)
        present = len([r for r in records if r['status'] == 'P'])
        absent = len([r for r in records if r['status'] == 'A'])
        late = 0
        by_subject = []

    # ==========================
    # SUBJECT TEACHER MODE
    # ==========================
    else:
        qs = AttendanceRecord.objects.filter(
            student=student,
            school=student.school,
            session__date__gte=target_monday,
            session__date__lte=target_friday,
        ).select_related("session__subject", "session__teacher")

        if selected_year_id:
            qs = qs.filter(session__term__academic_year_id=selected_year_id)

        records = qs.order_by('-session__date')

        subject_data = defaultdict(lambda: {'name': '', 'total': 0, 'present': 0, 'absent': 0})
        for record in records:
            if record.session and record.session.subject:
                subject = record.session.subject.name
                subject_data[subject]['name'] = subject
                subject_data[subject]['total'] += 1
                if record.status == 'P':
                    subject_data[subject]['present'] += 1
                else:
                    subject_data[subject]['absent'] += 1

        by_subject = []
        for subject in subject_data.values():
            subject['percentage'] = round((subject['present'] / subject['total']) * 100, 1) if subject['total'] else 0
            by_subject.append(subject)

        by_date_overall = defaultdict(list)
        for r in records:
            by_date_overall[r.session.date].append(r.status)

        total = len(by_date_overall)
        present = 0
        absent = 0
        for statuses in by_date_overall.values():
            if 'P' in statuses:
                present += 1
            else:
                absent += 1
        late = 0

    context = {
        'records': records,
        'total': total,
        'present': present,
        'absent': absent,
        'late': late,
        's_week_start': target_monday,
        's_week_end': target_friday,
        's_prev_week': s_prev_week,
        's_next_week': week_offset + 1,
        's_active_week': active_week,
        'attendance_mode': attendance_mode,
        'active_term': active_term,
        'by_subject': by_subject,
        # FILTER CONTEXT
        'years': years,
        'terms': terms,
        'selected_year': selected_year_id or "",
        'selected_term': selected_term_id or "",
        'school': student.school,
    }
    return render(request, 'accounts/student_attendance.html', context)

@login_required(login_url='accounts:login')
def dashboard_dispatcher(request):
    """This function catches all the broken 'dashboard' calls"""
    return redirect_to_dashboard(request.user)


@login_required
def export_attendance_excel(request, session_id):
    school = request.user.school
    if request.user.is_staff or request.user.is_superuser:
        session = get_object_or_404(AttendanceSession, id=session_id, school=school)
    else:
        session = get_object_or_404(
            AttendanceSession, 
            id=session_id, 
            teacher=request.user,
            school=school
        )

    # 2. NOW define records - must be after session
    records = AttendanceRecord.objects.filter(session=session, school=school).select_related('student').order_by('student__last_name', 'student__first_name')
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"

    # Header info
    ws['A1'] = f"Attendance Report"
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = f"Class: {session.school_class.name}"
    ws['A3'] = f"Subject: {session.subject.name}"
    ws['A4'] = f"Date: {session.date.strftime('%d %b, %Y')}"
    ws['A5'] = f"Teacher: {session.teacher.get_full_name() or session.teacher.username}"

    headers = ['No.', 'Student Name', 'Status', 'Remarks']
    ws.append([]) # blank row
    ws.append(headers)

    for cell in ws[7]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        cell.alignment = Alignment(horizontal="center")

    # Data rows
    for idx, record in enumerate(records.order_by('student__last_name'), 1):
        status = record.status.title()
        ws.append([
            idx,
            record.student.get_full_name() or record.student.username,
            status,
            "" # ks column
        ])

    # Auto-adjust column width
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[column].width = max_length + 2

    # Response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f"Attendance_{session.school_class.name}_{session.date}.xlsx"
    response['Content-Disposition'] = f'attachment; filename={filename}'
    wb.save(response)
    return response


@login_required
def admin_review_results(request):
    if request.user.role != "admin":
        return redirect("accounts:dashboard")

    school = request.user.school 

    results = (
        Result.objects.filter(status="submitted", school=school)  
        .select_related("student", "student__school_class", "subject")
        .order_by("student__school_class__name", "subject__name", "student__last_name")
    )

    class_id = request.GET.get("class")
    subject_id = request.GET.get("subject")
    term = request.GET.get("term")
    year = request.GET.get("year")

    if class_id:
        results = results.filter(student__school_class_id=class_id)
    if subject_id:
        results = results.filter(subject_id=subject_id)
    if term:
        results = results.filter(term__term_number=term)
    if year:
        results = results.filter(academic_year_id=year)

    classes = SchoolClass.objects.filter(school=school)  
    subjects = Subject.objects.filter(school=school)  
    years = AcademicYear.objects.filter(school=school).order_by("-name") 

    results = list(results)

    for r in results:
        assignment = TeacherSubjectClass.objects.filter(
            subject=r.subject,
            school_class=r.student.school_class,
            school_class__school=school,  
        ).first()

        r.teacher = (
            assignment.teacher.get_full_name()
            if assignment and assignment.teacher
            else "No Teacher"
        )

        ca = float(r.class_score or 0)
        exam = float(r.exam_score or 0)
        r.total = ca + exam

    return render(
        request,
        "accounts/admin_review_results.html",
        {
            "results": results,
            "classes": classes,
            "subjects": subjects,
            "years": years,
            "total_pending": len(results),
            "term_choices": Result.TERM_CHOICES,
        },
    )


@login_required
def approve_result(request, pk):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')
    
    school = request.user.school 
    
    result = get_object_or_404(Result, pk=pk, status='submitted', school=school)
    
    result.status = 'published'
    result.published_at = timezone.now()
    result.save()
    messages.success(request, f'Result for {result.student.get_full_name()} approved.')
    return redirect('accounts:admin_review_results')

@login_required
def return_result(request, pk):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')
    
    school = request.user.school
    
    result = get_object_or_404(Result, pk=pk, status='submitted', school=school)
    
    if request.method == 'POST':
        result.status = 'returned'
        result.returned_reason = request.POST.get('reason', '')
        result.save()
        messages.warning(request, f'Result returned to teacher.')
        
    return redirect('accounts:admin_review_results')

@login_required
def teacher_profile(request, user_id):
    school = request.user.school
    
    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
    
    teacher = get_object_or_404(User, id=user_id, role='teacher', school=school)
    
    assignments = TeacherSubjectClass.objects.filter(
        teacher=teacher,
        school=school  
    ).select_related('school_class', 'subject')
    
    allowed_class_ids = assignments.values_list('school_class_id', flat=True)
    
    total_students = User.objects.filter(
        school=school,
        school_class_id__in=allowed_class_ids
    ).count()
    
    context = {
        'teacher': teacher,
        'assignments': assignments,
        'total_students': total_students,
        'total_classes': assignments.values('school_class').distinct().count(),
    }
    return render(request, "accounts/teacher_profile.html", context)

@login_required
def add_subject_to_student(request, student_id):
    if request.method == 'POST':
        school = request.user.school
        
        student = get_object_or_404(
            User,
            id=student_id,
            role='student',
            school=school  
        )

        subject_ids = request.POST.getlist('subject_ids')
        
        if not student.school_class:
            messages.error(request, f'{student.first_name} has no class assigned')
            return redirect('accounts:view-student', student_id=student_id)

        added = 0
        for subject_id in subject_ids:
            subject = get_object_or_404(
                Subject,
                id=subject_id,
                school=school  
            )

            obj, created = StudentSubjectClass.objects.get_or_create(
                student=student,
                subject=subject,
                school_class=student.school_class,
                school=school,  
                defaults={
                    'school': school,  
                }
            )
            if created:
                added += 1

        messages.success(
            request,
            f'Added {added} new subject(s) to {student.first_name}'
        )

    return redirect(
        'accounts:view-student',
        student_id=student_id
    )


@login_required
def view_student_grades(request, student_id):
    school = request.user.school
    student = get_object_or_404(User, id=student_id, role='student', school=school)
    
    active_term = Term.objects.filter(is_active=True, school=school).first()
    
    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one in Term Settings.")
        return render(request, 'accounts/student_grades.html', {
            'student': student,
            'student_results': [],
            'active_term': None,
        })
    
    term = active_term
    year = active_term.academic_year
        
    student_results = Result.objects.filter(
        school=school,  
        student=student,
        term=term,
        academic_year=year,
        status__in=['submitted', 'published']
    ).select_related('subject').order_by('subject__name') 

    overall_average = 0
    overall_grade = "N/A"
    overall_remark = "N/A"
    
    if student_results.exists():
        total_percentage = 0
        valid_count = 0
        for result in student_results:
            try:
                if result.percentage is not None:
                    total_percentage += float(result.percentage)
                    valid_count += 1
            except (ValueError, TypeError):
                pass
        
        if valid_count > 0:
            overall_average = round(total_percentage / valid_count, 1)
            
            # Get overall grade from average
            if overall_average >= 80: overall_grade, overall_remark = "A", "Excellent"
            elif overall_average >= 70: overall_grade, overall_remark = "B", "Very Good"
            elif overall_average >= 60: overall_grade, overall_remark = "C", "Good"
            elif overall_average >= 50: overall_grade, overall_remark = "D", "Pass"
            elif overall_average >= 40: overall_grade, overall_remark = "E", "Weak"
            else: overall_grade, overall_remark = "F", "Fail"
    
    return render(request, 'accounts/student_grades.html', {
        'student': student,
        'student_results': student_results,
        'term': term,
        'academic_year': year,
        'active_term': active_term,
        'overall_average': overall_average,
        'overall_grade': overall_grade,
        'overall_remark': overall_remark,
        'ca_total': active_term.ca_total,
        'exam_total': active_term.exam_total,
    })


@login_required
def edit_student_grade(request, assignment_id):
    school = request.user.school
    
    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
    
    assignment = get_object_or_404(StudentSubjectClass, id=assignment_id, school=school)
    
    # Role check
    if request.user.role not in ['admin', 'teacher'] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')
    
    if request.method == 'POST':
        score = request.POST.get('score')
        grade = request.POST.get('grade')
        
        if score and score.strip() != "":
            try:
                assignment.score = int(score)
            except (ValueError, TypeError):
                messages.error(request, "Score must be a valid number")
                return render(request, 'accounts/edit_grade.html', {'assignment': assignment})
        else:
            assignment.score = None
            
        assignment.grade = grade.strip() if grade and grade.strip() else None
        
        assignment.save()
        messages.success(request, f'Grade updated for {assignment.subject.name} - {assignment.student}')
        return redirect('accounts:view-student-grades', student_id=assignment.student.id)
    
    return render(request, 'accounts/edit_grade.html', {
        'assignment': assignment
    })        


@login_required
def all_results(request):
    if not request.user.is_superuser:
        messages.error(request, "Admin only")
        return redirect('accounts:dashboard')
    
    school = request.user.school
    if not school:
        messages.error(request, "No school assigned to your account")
        return redirect('accounts:dashboard')

    results = Result.objects.filter(
        school=school,  # Direct isolation - most important!
        status__in=['submitted', 'published'],
    ).select_related(
        'student', 
        'subject', 
        'student__school_class',
        'academic_year',
        'term'
    ).order_by('-date', 'student__last_name')
    
    return render(request, 'accounts/all_results.html', {
        'results': results,
        'school': school
    })


@login_required
def results_by_class(request):
    if request.user.role != 'admin' and not request.user.is_superuser:
        return redirect('accounts:dashboard')
    
    school = request.user.school
    active_term = Term.objects.filter(is_active=True, school=school).first()
    
    if not active_term:
        return render(request, 'accounts/results_by_class.html', {
            'classes': [],
            'active_term': None,
            'error': 'No active term set. Ask admin to set one in Term Settings.'
        })
    
    term = request.GET.get('term')
    year_id = request.GET.get('year')

    if not term:
        term = active_term.term_number
    else:
        try:
            term = int(term)
        except (ValueError, TypeError):
            term = active_term.term_number

    if not year_id:
        year_id = active_term.academic_year.id
    else:
        try:
            year_id = int(year_id)
        except (ValueError, TypeError):
            year_id = active_term.academic_year.id

    year_obj = AcademicYear.objects.filter(id=year_id, school=school).first() # ✅ FIX 1 - add school
    
    classes = SchoolClass.objects.filter(
        school=school,
        students__results__status='published',
        students__results__school=school, 
        students__results__term__term_number=term,
        students__results__academic_year_id=year_id
    ).annotate(
        result_count=Count('students__results', filter=Q(students__results__status='published', students__results__school=school))
    ).distinct().order_by('name')

    terms = Term.objects.filter(school=school).order_by('term_number')
    years = AcademicYear.objects.filter(school=school).order_by('-name').distinct() 
    
    return render(request, 'accounts/results_by_class.html', {
        'classes': classes,
        'term': term,
        'year_obj': year_obj,
        'year_id': year_id,
        'active_term': active_term,
        'terms': terms,
        'years': years,
    })


@login_required
def results_by_subject(request, class_id):
    if request.user.role != 'admin' and not request.user.is_superuser:
        return redirect('accounts:dashboard')

    school = request.user.school
    school_class = get_object_or_404(SchoolClass, id=class_id, school=school)
    active_term = Term.objects.filter(is_active=True, school=school).first()

    if not active_term:
        return render(request, 'accounts/results_by_subject.html', {
            'school_class': school_class,
            'subjects': [],
            'active_term': None,
            'error': 'No active term set. Ask admin to set one in Term Settings.'
        })

    term_param = request.GET.get('term')
    year_param = request.GET.get('year')

    if not term_param or term_param == "":
        term = active_term.term_number
    else:
        try:
            term = int(term_param)
        except (ValueError, TypeError):
            term = active_term.term_number

    if not year_param or year_param == "":
        year = active_term.academic_year.id
    else:
        try:
            year = int(year_param)
        except (ValueError, TypeError):
            year = active_term.academic_year.id

    subjects = Subject.objects.filter(
        school=school,
        result__student__school_class=school_class,
        result__status='published',
        result__school=school,
        result__term__term_number=term,
        result__academic_year_id=year,
    ).annotate(
        student_count=Count('result', distinct=True)
    ).distinct().order_by('name')

    terms = Term.objects.filter(school=school).order_by('term_number')
    years = AcademicYear.objects.filter(school=school).order_by('-name').distinct()

    return render(request, 'accounts/results_by_subject.html', {
        'school_class': school_class,
        'subjects': subjects,
        'term': term,
        'year': year,
        'terms': terms,
        'years': years,
        'active_term': active_term,
    })


@login_required
def results_detail(request, class_id, subject_id):
    if request.user.role != "admin" and not request.user.is_superuser:
        return redirect("accounts:dashboard")

    school = request.user.school
    school_class = get_object_or_404(SchoolClass, id=class_id, school=school)
    subject = get_object_or_404(Subject, id=subject_id, school=school)
    active_term = Term.objects.filter(is_active=True, school=school).first()

    if not active_term:
        return render(
            request,
            "accounts/results_detail.html",
            {
                "school_class": school_class,
                "subject": subject,
                "results": [],
                "active_term": None,
                "error": "No active term set. Ask admin to set one in Term Settings.",
            },
        )

    # ✅ FIX - Safe handling for ?term=&year=1
    term_param = request.GET.get("term")
    year_param = request.GET.get("year")

    if not term_param or term_param.strip() == "":
        term = active_term.term_number
    else:
        try:
            term = int(term_param)
        except (ValueError, TypeError):
            term = active_term.term_number

    if not year_param or year_param.strip() == "":
        year = active_term.academic_year.id
    else:
        try:
            year = int(year_param)
        except (ValueError, TypeError):
            year = active_term.academic_year.id

    results = Result.objects.filter(
        school=school,
        student__school_class=school_class,
        subject=subject,
        status="published",
        term__term_number=term,
        academic_year__id=year,
    ).select_related("student", "subject", "academic_year", "term")

    results = list(results)

    for r in results:
        r.total = float(r.class_score or 0) + float(r.exam_score or 0)

    results.sort(key=lambda x: x.total, reverse=True)
    terms = Term.objects.filter(school=school).order_by("term_number")
    years = AcademicYear.objects.filter(school=school).order_by("-name").distinct()

    return render(
        request,
        "accounts/results_detail.html",
        {
            "school_class": school_class,
            "subject": subject,
            "results": results,
            "term": term,
            "year": year,
            "terms": terms,
            "years": years,
            "active_term": active_term,
        },
    )


@login_required
def print_class_report(request, class_id):

    school = request.user.school
    school_class = get_object_or_404(SchoolClass, id=class_id, school=school)
    active_term = Term.objects.filter(is_active=True, school=school).first()

    if not active_term:
        return HttpResponse("No active term set. Ask admin to set one in Term Settings.", status=400)

    term = int(request.GET.get('term', active_term.term_number))
    year = request.GET.get('year', active_term.academic_year.id)
    year = int(year)

    results = Result.objects.filter(
        school=school, # ✅ FIX 1 - direct school filter, more safe
        student__school_class=school_class,
        term__term_number=term,
        academic_year_id=year,
        status='published',
    ).select_related('student', 'subject') # ✅ add select_related for speed

    student_data = {}
    subjects = set()

    for r in results:
        student_name = f"{r.student.first_name} {r.student.last_name}"
        student_number = r.student.student_number

        if student_name not in student_data:
            student_data[student_name] = {
                "student_number": student_number,
                "subjects": {},
                "total": 0,
            }

        ca_total = ContinuousAssessment.objects.filter(
            school=school, # ✅ FIX 2 - add school if your model has it
            student=r.student,
            subject=r.subject,
            term__term_number=term,
            academic_year_id=year
        ).aggregate(total=Sum('score'))['total'] or 0

        exam = float(r.exam_score or 0)
        ca_total = float(ca_total or 0)
        total = ca_total + exam

        subject_code = r.subject.code or r.subject.name
        subjects.add(subject_code)

        student_data[student_name]["subjects"][subject_code] = {
            "ca": ca_total,
            "exam": exam,
            "total": total
        }

        student_data[student_name]["total"] += total

    sorted_students = sorted(student_data.items(), key=lambda x: x[1]['total'], reverse=True)

    for i, (name, data) in enumerate(sorted_students, 1):
        data['position'] = i

    subjects = sorted(list(subjects))
    return render(request, 'accounts/print_class_report.html', {
        'school_class': school_class,
        'term': term,
        'year': year,
        'active_term': active_term,
        'students': sorted_students,
        'subjects': subjects,
    })


def get_position_suffix(pos):
    if 11 <= pos % 100 <= 13:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(pos % 10, 'th')


@login_required
def print_student_report(request, student_id):
    school = request.user.school
    active_term = Term.objects.filter(is_active=True, school=school).first()
    if not active_term:
        return HttpResponse(
            "No active term set. Ask admin to set one in Term Settings.", status=400
        )

    term_number = int(request.GET.get("term") or active_term.term_number)
    year_id = int(request.GET.get("year") or active_term.academic_year.id)
    term_obj = get_object_or_404(
        Term, term_number=term_number, academic_year_id=year_id, school=school
    )

    if student_id == 0:
        class_id = request.GET.get("class_id")
        students = User.objects.filter(
            school_class_id=class_id, school=school, role="student"
        ).order_by("student_number")
    else:
        students = [get_object_or_404(User, id=student_id, school=school)]

    students_with_scores = []
    for s in students:
        results = Result.objects.filter(
            school=school,  # ✅ FIX 1
            student=s,
            term__term_number=term_number,
            academic_year_id=year_id,
            status="published",
        )
        student_total = 0
        for r in results:
            ca_total = (
                ContinuousAssessment.objects.filter(
                    school=school,  # ✅ FIX 2
                    student=r.student,
                    subject=r.subject,
                    term__term_number=term_number,
                    academic_year_id=year_id,
                ).aggregate(total=Sum("score"))["total"]
                or 0
            )
            student_total += float(ca_total or 0) + float(r.exam_score or 0)
        students_with_scores.append({"student": s, "total": student_total})

    students_with_scores.sort(key=lambda x: x["total"], reverse=True)
    position_map = {s["student"].id: i for i, s in enumerate(students_with_scores, 1)}

    student_class = students[0].school_class if students else None
    total_students_in_class = (
        User.objects.filter(
            role="student", school=school, school_class=student_class
        ).count()
        if student_class
        else 0
    )

    students_data = []
    for student in students:
        results = Result.objects.filter(
            school=school,  # ✅ FIX 3
            student=student,
            term__term_number=term_number,
            academic_year_id=year_id,
            status="published",
        )

        subjects_data = {}
        grand_total = 0
        for r in results:
            ca_total = (
                ContinuousAssessment.objects.filter(
                    school=school,  # ✅ FIX 4
                    student=r.student,
                    subject=r.subject,
                    term__term_number=term_number,
                    academic_year_id=year_id,
                ).aggregate(total=Sum("score"))["total"]
                or 0
            )
            exam = float(r.exam_score or 0)
            ca_total = float(ca_total or 0)
            total = ca_total + exam
            grand_total += total
            if total >= 80:
                grade, remark = "A", "Excellent"
            elif total >= 70:
                grade, remark = "B", "Very Good"
            elif total >= 60:
                grade, remark = "C", "Good"
            elif total >= 50:
                grade, remark = "D", "Pass"
            elif total >= 40:
                grade, remark = "E", "Weak"
            else:
                grade, remark = "F", "Fail"
            subject_code = r.subject.code or r.subject.name
            subjects_data[subject_code] = {
                "ca": ca_total,
                "exam": exam,
                "total": total,
                "grade": grade,
                "remark": remark,
            }

        num_subjects = len(subjects_data)
        average = grand_total / num_subjects if num_subjects > 0 else 0

        start = term_obj.start_date
        today = timezone.now().date()
        end = term_obj.end_date if term_obj.end_date < today else today

        holiday_dates = set()
        events = AcademicCalendar.objects.filter(
            school=school,
            start_date__lte=end,
            end_date__gte=start,
            affects_timetable=True,
        )
        for event in events:
            d = event.start_date
            while d <= event.end_date:
                if start <= d <= end:
                    holiday_dates.add(d)
                d += timedelta(days=1)

        attendance_records = (
            AttendanceRecord.objects.filter(
                student=student,
                session__school=school,  # ✅ FIX 5 - attendance school
                session__date__gte=start,
                session__date__lte=end,
            )
            .exclude(session__date__in=holiday_dates)
            .values("session__date", "status")
        )

        by_date = defaultdict(list)
        for rec in attendance_records:
            by_date[rec["session__date"]].append(rec["status"])

        times_present = sum(1 for statuses in by_date.values() if "P" in statuses)
        times_absent = sum(1 for statuses in by_date.values() if "P" not in statuses)
        times_late = 0
        times_excused = 0

        days_opened = 0
        d = start
        while d <= end:
            if d.weekday() < 5 and d not in holiday_dates:
                days_opened += 1
            d += timedelta(days=1)

        attendance_percentage = (
            round((times_present / days_opened) * 100, 1) if days_opened > 0 else 0
        )

        if average >= 80:
            overall_grade, overall_remark = "A", "Excellent"
        elif average >= 70:
            overall_grade, overall_remark = "B", "Very Good"
        elif average >= 60:
            overall_grade, overall_remark = "C", "Good"
        elif average >= 50:
            overall_grade, overall_remark = "D", "Pass"
        elif average >= 40:
            overall_grade, overall_remark = "E", "Weak"
        else:
            overall_grade, overall_remark = "F", "Fail"

        remark_obj = StudentRemark.objects.filter(
            school=school,  # ✅ FIX 6 - if your model has school, if not use student__school=school
            student=student,
            term__term_number=term_number,
            term__academic_year_id=year_id,
        ).first()

        students_data.append(
            {
                "student": student,
                "subjects": subjects_data,
                "grand_total": round(grand_total, 1),
                "average": round(average, 1),
                "grade": overall_grade,
                "overall_remark": overall_remark,
                "remark": overall_remark,
                "position": position_map.get(student.id, "-"),
                "position_suffix": get_position_suffix(position_map.get(student.id, 0)),
                "times_present": times_present,
                "times_absent": times_absent,
                "times_late": times_late,
                "times_excused": times_excused,
                "days_opened": days_opened,
                "next_term_date": term_obj.next_term_begins,
                "total_students_in_class": total_students_in_class,
                "attendance_percentage": attendance_percentage,
                "class_teacher_remark": (
                    remark_obj.class_teacher_remark if remark_obj else ""
                ),
                "headteacher_remark": (
                    remark_obj.headteacher_remark if remark_obj else ""
                ),
            }
        )

    return render(
        request,
        "accounts/print_student_report.html",
        {
            "students_data": students_data,
            "active_term": active_term,
            "term": term_obj,
            "year": year_id,
        },
    )


@login_required
def class_remarks_list(request):
    if not request.user.is_class_teacher and request.user.role != 'admin':
        return HttpResponse("Only class teachers can access this", status=403)
    
    school = request.user.school
    class_obj = request.user.class_teacher_of
    
    # ✅ Also verify class belongs to same school
    if not class_obj or class_obj.school_id != school.id:
        return HttpResponse("You are not assigned as class teacher of any class", status=403)
    
    term = Term.objects.filter(is_active=True, school=school).first()
    if not term:
        return HttpResponse("No active term found", status=403)
    
    students = User.objects.filter(
        role='student',
        school_class=class_obj,
        school=school
    ).order_by('last_name', 'first_name')
    
    remarks = []
    for student in students:
        remark, created = StudentRemark.objects.get_or_create(
            student=student,
            term=term,
            school=school, # ✅ FIX 1 - add school if model has it, if not add student__school=school check via filter before
            defaults={'school': school} # ✅ include in defaults too
        )
        remarks.append({'student': student, 'remark': remark})
    
    setting = SchoolSetting.objects.filter(school=school).first() # ✅ FIX 2 - was .first() for all schools
    attendance_mode = setting.attendance_mode if setting else "subject"
    
    return render(request, 'accounts/class_remarks.html', {
        'remarks': remarks, 
        'class_obj': class_obj,
        'attendance_mode': attendance_mode,
        'term': term,
    })

@login_required
def save_student_remark(request, student_id):
    if request.method == 'POST':
        school = request.user.school
        student = get_object_or_404(User, id=student_id, school=school)
        term = Term.objects.filter(is_active=True, school=school).first()
        
        if not term:
            return HttpResponse("No active term found", status=403)
        
        # ✅ Also check class school
        if not request.user.is_class_teacher or student.school_class != request.user.class_teacher_of or student.school_class.school_id != school.id:
            return HttpResponse("You can only edit remarks for your own class", status=403)
        
        remark, created = StudentRemark.objects.get_or_create(
            student=student,
            term=term,
            school=school, # ✅ FIX - add if your model has school field
            defaults={'school': school}
        )
        
        if request.user.is_class_teacher:
            remark.class_teacher_remark = request.POST.get('class_teacher_remark', '')
        
        if request.user.role in ['headteacher', 'admin', 'headmaster']:
            remark.headteacher_remark = request.POST.get('headteacher_remark', '')
            
        remark.save()
        return HttpResponse("Remarks saved successfully")
    
    return HttpResponse("Invalid request", status=400)


@login_required
def add_subject(request):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        name = request.POST.get('name')
        code = request.POST.get('code')

        existing_subject = Subject.objects.filter(
            school=request.user.school,
            name__iexact=name
        ).first()

        if existing_subject:
            messages.warning(request, 'Subject already exists in your school.')
            return redirect('accounts:add_subject')

        Subject.objects.create(
            name=name,
            code=code,
            school=request.user.school
        )

        messages.success(request, 'Subject added successfully')
        return redirect('accounts:subject_list')

    return render(request, 'accounts/add_subject.html', {
    'school': request.user.school
})


@login_required
@user_passes_test(lambda u: u.is_staff)
def set_active_term(request):
    if request.method == "POST":
        school = request.user.school
        ca = request.POST.get("ca_total")
        exam = request.POST.get("exam_total")
        term_id = request.POST.get("term_id")

        if not term_id:
            messages.error(request, "Select a term to activate")
            return redirect("term_settings")

        # ✅ FIX 1: Verify term belongs to my school
        term = get_object_or_404(Term, id=term_id, school=school)

        # ✅ FIX 2: Deactivate only MY school terms
        Term.objects.filter(is_active=True, school=school).update(is_active=False)

        # Activate the selected term
        term.is_active = True
        term.save()

        # Update CA/Exam totals if provided
        if ca is not None and exam is not None:
            TermSetting.objects.update_or_create(
                term=term, defaults={"ca_total": ca, "exam_total": exam}
            )

        messages.success(
            request, f"Active term set to: {term.academic_year} Term {term.term_number}"
        )

    return redirect("term_settings")


@login_required
def term_settings(request):
    
    active_term = Term.objects.filter(
        is_active=True,
        school=request.user.school
    ).first()
    

    if request.method == 'POST':
        form = TermSettingForm(request.POST)

        if form.is_valid():
            term = form.save(commit=False)
            term.school = request.user.school
            

            # calculate TOTAL school days
            total_days = calculate_school_days(
                term.start_date,
                term.end_date,
                term.school
            )
            term.days_opened = total_days



            if term.is_active:
                Term.objects.filter(
                    school=request.user.school,
                    is_active=True
                ).update(is_active=False)

            term.save()

            messages.success(request, "Term saved successfully")
            return redirect('accounts:term_settings')

    else:
        form = TermSettingForm()

    terms = Term.objects.filter(
        school=request.user.school
    ).order_by('-academic_year', '-term_number')

    # ALSO calculate for active banner


    return render(request, 'accounts/term_settings.html', {
        'form': form,
        'terms': terms,
        'active_term': active_term,
    })


@login_required
def edit_term(request, pk):
    school = request.user.school
    term = get_object_or_404(Term, pk=pk, school=school)
    
    if request.method == 'POST':
        form = TermForm(request.POST, instance=term)
        if form.is_valid():
            if form.cleaned_data['is_active']:
                Term.objects.filter(is_active=True, school=school).update(is_active=False) 
            
            edited_term = form.save(commit=False)
            edited_term.school = school
            edited_term.save()
            
            messages.success(request, "Term updated")
            return redirect('accounts:term_settings')
        else:
            messages.error(request, "Error: " + str(form.errors))
    else:
        form = TermForm(instance=term)
        
    return render(request, 'accounts/edit_term.html', {'form': form})


@login_required
@user_passes_test(lambda u: u.is_staff)
def delete_term(request, pk):
    school = request.user.school
    term = get_object_or_404(Term, pk=pk, school=school) 
    term.delete()
    messages.success(request, "Term deleted")
    return redirect('accounts:term_settings')


@login_required
def add_class(request):
    school = request.user.school

    if request.method == "POST":
        form = SchoolClassForm(request.POST, request=request)

        if form.is_valid():
            school_class = form.save(commit=False)

            # SCHOOL ISOLATION
            school_class.school = school

            existing_class = SchoolClass.objects.filter(
                school=school,
                name__iexact=school_class.name
            ).exclude(stage='').first()

            if existing_class:
                school_class.stage = existing_class.stage

            school_class.save()

            return redirect('accounts:admin_classes')

        else:
            print("FORM ERRORS:", form.errors)

    else:
        form = SchoolClassForm(request=request)

    return render(
        request,
        'accounts/add_class.html',
        {
            'form': form,
            'school': school,
        }
    )


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@role_required(['hod', 'admin'])
def timetable_manager_create_timetable(request):
    school = request.user.school
    if request.method == 'POST':
        teacher_id = request.POST.get('teacher_id')
        subject_id = request.POST.get('subject_id')
        class_id = request.POST.get('school_class_id')
        weekday = request.POST.get('weekday')
        period = request.POST.get('period')
        start_time = request.POST.get('start_time') or None
        end_time = request.POST.get('end_time') or None
        # Handle Break creation
        if 'add_break' in request.POST:

            stage = request.POST.get('stage')
            break_name = request.POST.get('break_name')
            break_start = request.POST.get('break_start_time')
            break_end = request.POST.get('break_end_time')
            break_start = datetime.strptime(break_start, "%H:%M").time()
            break_end = datetime.strptime(break_end, "%H:%M").time()


            lessons = Timetable.objects.filter(
                school_class__school=school,
                school_class__stage=stage
            )


            for lesson in lessons:

                if break_start < lesson.end_time and break_end > lesson.start_time:

                    messages.error(
                        request,
                        "Cannot create break. This time already has a lesson."
                    )

                    return redirect('accounts:timetable_create')

            try:
                Break.objects.create(
                    school=school,
                    stage=stage,
                    name=break_name,
                    start_time=break_start,
                    end_time=break_end
                )

                messages.success(
                    request,
                    "Break created successfully"
                )

            except Exception as e:
                messages.error(
                    request,
                    f"Break error: {str(e)}"
                )

            return redirect('accounts:timetable_create')

        # Handle delete
        if 'delete_timetable' in request.POST:
            Timetable.objects.filter(id=request.POST.get('timetable_id')).delete()
            messages.success(request, "Timetable entry deleted")
            return redirect('accounts:timetable_create')

        # Handle create/add
        if 'add_timetable' in request.POST or 'teacher_id' in request.POST:
            # Check lesson against breaks
            school_class = get_object_or_404(
                SchoolClass,
                id=class_id,
                school=school
            )

            stage_breaks = Break.objects.filter(
                school=school,
                stage=school_class.stage
            )


            lesson_start = datetime.strptime(start_time, "%H:%M").time()
            lesson_end = datetime.strptime(end_time, "%H:%M").time()

            for br in stage_breaks:
                # Overlap test
                if lesson_start < br.end_time and lesson_end > br.start_time:
                    messages.error(
                        request,
                        f"Cannot create lesson. It overlaps with '{br.name}' "
                        f"({br.start_time.strftime('%H:%M')} - {br.end_time.strftime('%H:%M')})."
                    )
                    return redirect("accounts:timetable_create")
            # CHECK FOR CLASH - no exclude_id for create
            clashes = check_clash(school, teacher_id, class_id, weekday, start_time, end_time)
            if clashes.exists():
                for clash in clashes:
                    if clash.teacher_id == int(teacher_id):
                        messages.error(request, f"Clash: {clash.teacher.get_full_name()} is already teaching {clash.subject.name} to {clash.school_class.name} on {clash.get_weekday_display()} at {clash.start_time}-{clash.end_time}")
                    if clash.school_class_id == int(class_id):
                        messages.error(request, f"Clash: {clash.school_class.name} already has {clash.subject.name} with {clash.teacher.get_full_name()} at this time")
                return redirect('accounts:timetable_create')

            # If no clash, create it
            try:
                Timetable.objects.create(
                    teacher_id=teacher_id,
                    subject_id=subject_id,
                    school_class_id=class_id,
                    weekday=weekday,
                    period=period,
                    start_time=start_time,
                    end_time=end_time
                )
                messages.success(request, "Timetable entry created successfully")
            except Exception as e:
                messages.error(request, f"Error: {str(e)}")
            
            return redirect('accounts:timetable_create')

    # GET request - show the page
    teachers = User.objects.filter(role='teacher', school=school).order_by('first_name')
    subjects = Subject.objects.filter(school=school).order_by('name')
    classes = SchoolClass.objects.filter(school=school).order_by('name')
    stages = SchoolClass.objects.filter(school=school).values_list('stage', flat=True).distinct().order_by('stage')
    existing_timetables = Timetable.objects.select_related('teacher', 'subject', 'school_class').filter(school_class__school=school).order_by('weekday', 'period')
    breaks = Break.objects.filter(school=school).order_by('stage', 'start_time')
    weekdays = [(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday')]
    teacher_count = teachers.count()
    class_count = classes.count()
    subject_count = subjects.count()
    timetable_count = existing_timetables.count()

    return render(request, 'accounts/timetable_create.html', {
        "teacher_count": teacher_count,
        "class_count": class_count,
        "subject_count": subject_count,
        "timetable_count": timetable_count,
        'teachers': teachers,
        'subjects': subjects,
        'classes': classes,
        'stages': stages,
        'timetables': existing_timetables,
        'breaks': breaks,
        'weekdays': weekdays,
    })

@login_required
def manage_timetable(request):

    school = request.user.school
    selected_stage = request.GET.get("stage")
    selected_class = request.GET.get("class")

    teachers = User.objects.filter(
        role='teacher',
        school=school
    ).order_by('first_name')


    subjects = Subject.objects.filter(
        school=school
    ).order_by('name')


    classes = SchoolClass.objects.filter(
        school=school
    ).order_by('name')


    existing_timetables = Timetable.objects.select_related(
        'teacher',
        'subject',
        'school_class'
    ).filter(
        school_class__school=school
    ).order_by(
        'weekday',
        'start_time'
    )


    breaks = Break.objects.filter(
        school=school
    ).order_by(
        'stage',
        'start_time'
    )


    weekdays = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
    ]
    timetable_data = defaultdict(lambda: defaultdict(list))

    for item in existing_timetables:
        stage = item.school_class.stage
        class_name = item.school_class.name

        timetable_data[stage][class_name].append(item)


    classes_by_stage = {}

    for stage, class_dict in timetable_data.items():
        classes_by_stage[stage] = list(class_dict.keys())


    stages = timetable_data.keys()
    filtered_timetable = Timetable.objects.filter(
        school_class__school=school
    )

    if selected_stage:
        filtered_timetable = filtered_timetable.filter(
            school_class__stage=selected_stage
        )

    if selected_class:
        filtered_timetable = filtered_timetable.filter(
            school_class__name=selected_class
        )

    filtered_timetable = filtered_timetable.select_related(
        "teacher",
        "subject",
        "school_class"
    ).order_by(
        "weekday",
        "start_time"
    )
    selected_breaks = Break.objects.filter(
    school=school
    )

    if selected_stage:
        selected_breaks = selected_breaks.filter(
            stage=selected_stage
        )

    selected_breaks = selected_breaks.order_by(
        "start_time"
    )


    timetable_grid = defaultdict(dict)

    time_slots = set()


    # Add lessons to timetable grid
    for item in filtered_timetable:

        time_range = (
            f"{item.start_time.strftime('%H:%M')} - "
            f"{item.end_time.strftime('%H:%M')}"
        )

        time_slots.add(time_range)

        timetable_grid[item.weekday][time_range] = item



    # Add break times to timeline
    for br in breaks.filter(stage=selected_stage):

        break_range = (
            f"{br.start_time.strftime('%H:%M')} - "
            f"{br.end_time.strftime('%H:%M')}"
        )

        time_slots.add(break_range)



    # Ensure all weekdays exist
    for day_value, _ in weekdays:

        if day_value not in timetable_grid:
            timetable_grid[day_value] = {}



    # Sort timeline
    time_slots = sorted(time_slots)



    # Break data
    break_slots = []

    for br in breaks:

        break_slots.append({
            "id": br.id,
            "stage": br.stage,
            "name": br.name,
            "start": br.start_time,
            "end": br.end_time,
            "label": (
                f"{br.start_time.strftime('%H:%M')} - "
                f"{br.end_time.strftime('%H:%M')}"
            )
        })
        break_times = []

        for br in breaks.filter(stage=selected_stage):

            break_times.append(
                f"{br.start_time.strftime('%H:%M')} - {br.end_time.strftime('%H:%M')}"
            )
        


    return render(request, "accounts/manage_timetable.html", {
        "teachers": teachers,
        "subjects": subjects,
        "school_classes": classes,
        "timetables": existing_timetables,
        "breaks": breaks,
        "stages": stages,
        "weekdays": weekdays,
        "time_slots": time_slots,
        "break_slots": break_slots,
        "break_times": break_times,
        "classes_by_stage": classes_by_stage,
        "timetable_data": timetable_data,
        "filtered_timetable": filtered_timetable,
        "timetable_grid": timetable_grid,
        "selected_stage": selected_stage,
        "selected_class": selected_class,
    })


@role_required(['hod', 'admin'])
def timetable_manager_delete(request, timetable_id):

    school = request.user.school

    timetable = get_object_or_404(
        Timetable,
        id=timetable_id,
        school_class__school=school
    )

    timetable.delete()

    messages.success(
        request,
        "Timetable entry deleted"
    )

    return redirect('accounts:timetable_create')


@login_required
def student_timetable(request):
    if request.user.role != 'student':
        return redirect('accounts:student_dashboard')

    student = request.user
    filter_data = get_term_year_filter(request, student.school)
    years = filter_data["years"]
    today_date = timezone.now().date()

    # Get selected values
    selected_year = request.GET.get('year')
    selected_term = request.GET.get('term')

    # First time = use default from system
    if not selected_year and not selected_term:
        selected_year = filter_data.get("selected_year")
        selected_term = filter_data.get("selected_term")

    sy = str(selected_year) if selected_year else ""
    st = str(selected_term) if selected_term else ""

    # If one is missing, show empty
    if not sy or not st:
        return render(request, 'accounts/student_timetable.html', {
            **filter_data, 'years': years,
            'selected_year': sy, 'selected_term': st,
            'timetable_by_day': [], 'today': today_date,
            'need_both': True, 'timetable_total_weeks': 1,
            'timetable_active_week': 1, 'timetable_today_week': 1
        })

    active_term = Term.objects.filter(
        school=student.school,
        academic_year_id=sy,
        term_number=st
    ).first()

    if not active_term:
        return render(request, 'accounts/student_timetable.html', {
            **filter_data, 'years': years,
            'selected_year': sy, 'selected_term': st,
            'timetable_by_day': [], 'today': today_date,
            'no_timetable': True, 'timetable_total_weeks': 1,
            'timetable_active_week': 1, 'timetable_today_week': 1
        })

    # Week calculation (same as before)
    term_start = active_term.start_date
    term_end = active_term.end_date

    # Calculate what is the CURRENT week number
    current_week_num = 1
    cur = term_start
    today = today_date
    week_counter = 1
    while cur <= term_end:
        friday = cur + timedelta(days=(4 - cur.weekday()))
        week_end_calc = friday if friday <= term_end else term_end
        if cur <= today <= week_end_calc:
            current_week_num = week_counter
            break
        cur = friday + timedelta(days=3)
        week_counter += 1

    week_param = request.GET.get('t_week') or request.GET.get('week')
    try:
        active_week = int(week_param) if week_param else current_week_num
    except:
        active_week = current_week_num

    total_weeks = 0
    cur = term_start
    while cur <= term_end:
        total_weeks += 1
        friday = cur + timedelta(days=(4 - cur.weekday()))
        cur = friday + timedelta(days=3)

    active_week = max(1, min(active_week, total_weeks))

    week_start = term_start
    for _ in range(active_week - 1):
        friday = week_start + timedelta(days=(4 - week_start.weekday()))
        week_start = friday + timedelta(days=3)
    week_end = week_start + timedelta(days=(4 - week_start.weekday()))
    if week_end > term_end:
        week_end = term_end

    timetable = Timetable.objects.filter(school_class=student.school_class).select_related('subject').order_by('weekday', 'period')
    
    timetable_by_day = []
    cur_date = week_start
    while cur_date <= week_end:
        if cur_date.weekday() < 5:
            event = AcademicCalendar.objects.filter(start_date__lte=cur_date, end_date__gte=cur_date).first()
            entries = [e for e in timetable if e.weekday == cur_date.weekday()]
            if event and event.affects_timetable:
                entries = []
            timetable_by_day.append({'num': cur_date.weekday(), 'name': cur_date.strftime("%A"), 'date': cur_date, 'entries': entries, 'event': event})
        cur_date += timedelta(days=1)
        if cur_date.weekday() == 0 and timetable_by_day:
            break

    return render(request, 'accounts/student_timetable.html', {
        **filter_data, 'years': years, 'selected_year': sy, 'selected_term': st,
        'timetable_by_day': timetable_by_day, 'today': today_date,
        'today_weekday': today_date.weekday() if term_start <= today_date <= term_end else -1,
        'timetable_active_week': active_week,
        'timetable_prev_week': active_week - 1 if active_week > 1 else None,
        'timetable_next_week': active_week + 1 if active_week < total_weeks else None,
        'timetable_total_weeks': total_weeks,
        'current_week_start': week_start, 'current_week_end': week_end,
        'active_term': active_term, 'timetable_today_week': current_week_num,
    })


@role_required(['hod', 'admin'])
def timetable_manager_edit(request, timetable_id):

    school = request.user.school

    timetable = get_object_or_404(
        Timetable,
        id=timetable_id,
        school_class__school=school
    )

    if request.method == "POST":

        teacher_id = request.POST.get('teacher_id')
        subject_id = request.POST.get('subject_id')
        class_id = request.POST.get('school_class_id')

        school_class = get_object_or_404(
            SchoolClass,
            id=class_id,
            school=school
        )

        weekday = request.POST.get('weekday')
        period = request.POST.get('period')

        start_time = request.POST.get('start_time') or None
        end_time = request.POST.get('end_time') or None

        if start_time and end_time:
            start_time = datetime.strptime(start_time, "%H:%M").time()
            end_time = datetime.strptime(end_time, "%H:%M").time()


        # CHECK BREAK CLASH
        stage_breaks = Break.objects.filter(
            school=school,
            stage=school_class.stage
        )

        for br in stage_breaks:

            if start_time < br.end_time and end_time > br.start_time:

                messages.error(
                    request,
                    f"Cannot update lesson. It overlaps with '{br.name}' "
                    f"({br.start_time.strftime('%H:%M')} - {br.end_time.strftime('%H:%M')})."
                )

                return redirect(
                    "accounts:timetable_manager_edit",
                    timetable_id=timetable.id
                )


    # YOUR EXISTING check_clash() HERE

        clashes = check_clash(
            school,
            teacher_id,
            class_id,
            weekday,
            start_time,
            end_time,
            exclude_id=timetable.id
        )


        if clashes.exists():
            for clash in clashes:

                if clash.teacher_id == int(teacher_id):
                    messages.error(
                        request,
                        f"{clash.teacher.get_full_name()} already has "
                        f"{clash.subject.name} at this time."
                    )

                if clash.school_class_id == int(class_id):
                    messages.error(
                        request,
                        f"{clash.school_class.name} already has "
                        f"a lesson at this time."
                    )

            return redirect(
                'accounts:timetable_manager_edit',
                timetable_id=timetable.id
            )


        timetable.teacher_id = teacher_id
        timetable.subject_id = subject_id
        timetable.school_class = school_class
        timetable.weekday = weekday
        timetable.period = period
        timetable.start_time = start_time
        timetable.end_time = end_time

        timetable.save()

        messages.success(
            request,
            "Timetable updated successfully"
        )


    return redirect('accounts:timetable_create')


@role_required(['hod','admin'])
def break_edit(request, break_id):

    school = request.user.school

    br = get_object_or_404(
        Break,
        id=break_id,
        school=school
    )

    if request.method == "POST":

        stage = request.POST.get("stage")
        name = request.POST.get("break_name")
        start = request.POST.get("break_start_time")
        end = request.POST.get("break_end_time")



        start_time = datetime.strptime(
            start,
            "%H:%M"
        ).time()

        end_time = datetime.strptime(
            end,
            "%H:%M"
        ).time()


        lessons = Timetable.objects.filter(
            school_class__school=school,
            school_class__stage=stage
        ).exclude(
            id=getattr(br, 'timetable_id', None)
        )


        for lesson in lessons:

            if start_time < lesson.end_time and end_time > lesson.start_time:

                messages.error(
                    request,
                    f"Cannot update break. It overlaps with "
                    f"{lesson.subject.name} ({lesson.start_time.strftime('%H:%M')} - {lesson.end_time.strftime('%H:%M')})."
                )

                return redirect(
                    "accounts:manage_timetable"
                )
        # to prevent break covering lessons


        br.stage = stage
        br.name = name
        br.start_time = start
        br.end_time = end

        br.save()


        messages.success(
            request,
            "Break updated successfully"
        )


    return redirect(
        "accounts:manage_timetable"
    )


@login_required
def calendar_list(request):

    school = request.user.school

    events = AcademicCalendar.objects.filter(
        school=school
    ).order_by('start_date')

    return render(
        request,
        'accounts/calendar_list.html',
        {
            'events': events
        }
    )


@login_required
def add_calendar_event(request):
    if request.method == 'POST':
        form = AcademicCalendarForm(request.POST)

        if form.is_valid():

            name = form.cleaned_data['name']
            start_date = form.cleaned_data['start_date']
            overlap = AcademicCalendar.objects.filter(
                school=request.user.school,
                start_date__lte=form.cleaned_data['end_date'],
                end_date__gte=start_date
            ).exists()


            if overlap:
                messages.error(
                    request,
                    "This event overlaps with an existing academic calendar event."
                )
                return redirect('accounts:calendar_list')

            if AcademicCalendar.objects.filter(
                school=request.user.school,
                name=name,
                start_date=start_date
            ).exists():
                messages.error(request, 'This event already exists!')
                return redirect('accounts:calendar_list')

            event = form.save(commit=False)
            event.school = request.user.school
            event.save()

            active_term = Term.objects.filter(
                school=request.user.school,
                is_active=True
            ).first()

            if active_term:
                active_term.days_opened = calculate_school_days(
                    active_term.start_date,
                    active_term.end_date,
                    active_term.school
                )
                active_term.save()

            messages.success(request, 'Event added successfully!')
            return redirect('accounts:calendar_list')

        else:
            print(form.errors)
            print(request.POST)

    else:
        form = AcademicCalendarForm()

    return render(request, 'accounts/add_event.html', {'form': form})

@login_required
def edit_event(request, pk):

    event = get_object_or_404(
        AcademicCalendar,
        id=pk,
        school=request.user.school
    )

    if request.method == "POST":

        event.name = request.POST.get("name")
        event.start_date = request.POST.get("start_date")
        event.end_date = request.POST.get("end_date")


        overlap = AcademicCalendar.objects.filter(
            school=request.user.school,
            start_date__lte=event.end_date,
            end_date__gte=event.start_date
        ).exclude(
            id=event.id
        ).exists()


        if overlap:
            messages.error(
                request,
                "Updated dates overlap with another calendar event."
            )
            return redirect(
                "accounts:calendar_list"
            )


        try:

            event.full_clean()
            event.save()

            messages.success(
                request,
                "Event updated successfully"
            )

        except ValidationError as e:

            messages.error(
                request,
                e.messages[0]
            )


    return redirect(
        "accounts:calendar_list"
    )

@login_required
def delete_event(request, pk):

    event = get_object_or_404(
        AcademicCalendar,
        id=pk,
        school=request.user.school
    )

    event.delete()

    messages.success(
        request,
        "Event deleted successfully"
    )

    return redirect(
        "accounts:calendar_list"
    )


@login_required
def add_accountant(request):
    credentials = None

    if request.user.role != 'admin':
        return redirect('accounts:home')

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect('accounts:home')

    if request.method == "POST":
        form = AccountantForm(request.POST, request.FILES)

        if form.is_valid():
            accountant = form.save(commit=False)

            accountant.role = 'accountant'
            accountant.school = request.user.school
            accountant.is_password_changed = False
            accountant.is_active = True

            base_username = f"{accountant.first_name.lower()}.{accountant.last_name.lower()}"
            username = base_username
            counter = 1

            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            accountant.username = username

            password = form.cleaned_data.get('password') or 'Staff2026'
            accountant.set_password(password)

            try:
                accountant.save()
                form.save_m2m()
            except Exception as e:
                messages.error(request, f"Database error: {str(e)}")
                return redirect('accounts:add-accountant')
            request.session["credentials"] = {
                "title": "Accountant Login Credentials",
                "username": accountant.username,
                "staff_id": accountant.staff_id,
                "password": password}

            return redirect('accounts:manage_accountant_grid')

        else:
            messages.error(request, "Please fix the errors below.")

    else:
        form = AccountantForm()

    return render(request, "accounts/add_accountant.html", {
        "form": form,
        "credentials": credentials
    })


@login_required
def manage_accountant_grid(request):
    if request.user.role != 'admin':
        return redirect('accounts:home')
    credentials = request.session.pop("credentials", None)
    
    search_query = request.GET.get('q', '')
    
    accountants = User.objects.filter(
        role='accountant', is_active=True,
        school=request.user.school
    )
    
    if search_query:
        accountants = accountants.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(username__icontains=search_query) |
            Q(staff_id__icontains=search_query)   
        )
    
    accountants = accountants.order_by('first_name')
    paginator = Paginator(accountants, 10)
    page = request.GET.get('page')
    accountants = paginator.get_page(page)
    
    return render(request, "accounts/manage_accountant_grid.html", {
        "accountants": accountants,
        "search_query": search_query,
            'credentials': credentials
    })

@login_required
def view_accountant(request, accountant_id):
    if request.user.role != 'admin':
        return redirect('accounts:home')
    
    accountant = get_object_or_404(User, id=accountant_id, role='accountant', school=request.user.school)
    return render(request, "accounts/view_accountant.html", {"accountant": accountant})

@login_required
def edit_accountant(request, accountant_id):
    if request.user.role != 'admin':
        return redirect('accounts:home')
        
    accountant = get_object_or_404(User, id=accountant_id, role='accountant', school=request.user.school)
    
    if request.method == 'POST':
        form = AccountantEditForm(request.POST, request.FILES, instance=accountant)  
        if form.is_valid():
            form.save()
            messages.success(request, 'Accountant updated successfully.')
            return redirect('accounts:view_accountant', accountant_id=accountant.id)
    else:
        form = AccountantEditForm(instance=accountant)  
    
    return render(request, "accounts/edit_accountant.html", {
        "form": form,
        "accountant": accountant
    })

@login_required
def accountant_profile(request):
    # If logged in user is accountant, pass them as 'accountant'
    context = {
        'accountant': request.user
    }
    return render(request, 'accounts/accountant_profile.html', context)


@login_required
def delete_accountant(request, accountant_id):
    if request.user.role != 'admin':
        return redirect('accounts:home')
    if request.method == "POST":
        User.objects.filter(
            id=accountant_id, 
            role='accountant',
            school=request.user.school  # ✅ add this
        ).update(is_active=False)
        messages.success(request, "Accountant deactivated.")
    return redirect('accounts:manage_accountant_grid')

@login_required
def deactivated_accountants(request):
    if request.user.role != 'admin':
        return redirect('accounts:home')
        
    accountants = User.objects.filter(
        role='accountant', 
        is_active=False,  
        school=request.user.school
    ).order_by('-date_joined')
    return render(request, "accounts/deactivated_accountants.html", {"accountants": accountants})

@login_required  
def reactivate_accountant(request, user_id):
    if request.user.role != 'admin':
        return redirect('accounts:home')
        
    accountant = get_object_or_404(User, id=user_id, role='accountant', school=request.user.school)
    accountant.is_active = True  
    accountant.save()
    messages.success(request, f"{accountant.get_full_name()} has been reactivated.")
    return redirect('accounts:deactivated_accountants')


@login_required
def accountant_dashboard(request):
    if request.user.role!= 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    today = date.today()

    active_term = Term.objects.filter(school=school, is_active=True).first()

    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one in Term Settings.")
        return render(request, 'accounts/accountant_dashboard.html', {
            'active_term': None,
            'total_collected': 0,
            'total_outstanding': 0,
            'total_expected': 0,
            'total_defaulters': 0,
        })

    term_label = f"Term {active_term.term_number}"
    academic_year = active_term.academic_year

    # Base querysets
    fees_this_term = StudentFee.objects.filter(
        student__school=school,
        term=active_term,
        academic_year=academic_year
    ).select_related('student', 'student__school_class')

    payments_this_term = PaymentTransaction.objects.filter(
        student__school=school,
        term=active_term,
        academic_year=academic_year
    )

    # 1. Total Collected This Term - FIXED: use total_amount not amount_paid
    total_collected = payments_this_term.aggregate(total=Sum('total_amount'))['total'] or 0

    # 2. Outstanding + Expected - FIXED: calculate from transactions not fee.balance()
    total_expected = fees_this_term.aggregate(total=Sum('total_amount'))['total'] or 0

    # Get real paid amounts per student from transactions
    student_paid_map = {}
    for txn in payments_this_term:
        sid = txn.student_id
        student_paid_map[sid] = student_paid_map.get(sid, 0) + float(txn.total_amount or 0)

    total_outstanding = 0
    for fee in fees_this_term:
        paid = student_paid_map.get(fee.student_id, 0)
        balance = float(fee.total_amount) - paid
        if balance > 0:
            total_outstanding += balance

    # 3. Today's collections - FIXED
    display_date = today
    today_collections = payments_this_term.filter(
        created_at__date=today
    ).aggregate(total=Sum('total_amount'))['total'] or 0

    # 4. Total Expenses This Term - filter by term date range, not year
    total_expenses = Expense.objects.filter(
        school=school,
        expense_date__range=(active_term.start_date, active_term.end_date)
    ).aggregate(total=Sum('amount'))['total'] or 0

    # 5. Total Students
    total_students = User.objects.filter(school=school, role='student', is_active=True).count()

    # 6. TODAY'S payments grouped by class - limit 5 per class
    today_payments = PaymentTransaction.objects.filter(
        student__school=school,
        created_at__date=today
    ).select_related(
        'student',
        'student__school_class'
    ).order_by('-created_at')

    payments_by_class = {}
    for payment in today_payments:
        class_name = payment.student.school_class.name if payment.student and payment.student.school_class else "No Class"

        payments_by_class.setdefault(class_name, [])
        if len(payments_by_class[class_name]) < 5:
            payments_by_class[class_name].append(payment)

    # 7. Fee defaulters COUNT ONLY + GROUP BY STAGE - FIXED: use transaction sums
    defaulter_counts = {}
    defaulter_counts_by_stage = {stage: 0 for stage in STAGE_GROUP_MAP.keys()}
    total_defaulters = 0

    for fee in fees_this_term.select_related('student__school_class'):
        paid = student_paid_map.get(fee.student_id, 0)
        balance = float(fee.total_amount) - paid

        if balance > 0:
            class_name = fee.student.school_class.name if fee.student.school_class else "Unassigned"
            defaulter_counts[class_name] = defaulter_counts.get(class_name, 0) + 1
            total_defaulters += 1

            for stage, classes in STAGE_GROUP_MAP.items():
                if class_name in classes:
                    defaulter_counts_by_stage[stage] += 1
                    break

    defaulter_counts = dict(sorted(defaulter_counts.items()))

    # 8. Aggregate fees + canteen by stage - FIXED: use transaction sums
    fees_by_stage = {stage: {'expected': 0, 'collected': 0, 'outstanding': 0, 'student_count': 0}
                    for stage in STAGE_GROUP_MAP.keys()}
    canteen_by_stage = {stage: {'expected': 0, 'collected': 0, 'outstanding': 0}
                        for stage in STAGE_GROUP_MAP.keys()}

    for fee in fees_this_term.select_related('student__school_class'):
        if not fee.student.school_class:
            continue

        stage_match = fee.student.school_class.stage
        if not stage_match:
            continue

        paid = student_paid_map.get(fee.student_id, 0)
        balance = float(fee.total_amount) - paid

        fees_by_stage[stage_match]['expected'] += float(fee.total_amount)
        fees_by_stage[stage_match]['collected'] += paid
        fees_by_stage[stage_match]['outstanding'] += balance if balance > 0 else 0
        fees_by_stage[stage_match]['student_count'] += 1

        canteen_expected = float(fee.canteen_amount or 0)
        # Note: amount_paid_canteen might still be outdated - better to track in PaymentTransactionItem
        canteen_collected = float(fee.amount_paid_canteen or 0)
        canteen_outstanding = canteen_expected - canteen_collected

        canteen_by_stage[stage_match]['expected'] += canteen_expected
        canteen_by_stage[stage_match]['collected'] += canteen_collected
        canteen_by_stage[stage_match]['outstanding'] += canteen_outstanding

    canteen_totals = {
        'expected': sum(s['expected'] for s in canteen_by_stage.values()),
        'collected': sum(s['collected'] for s in canteen_by_stage.values()),
        'outstanding': sum(s['outstanding'] for s in canteen_by_stage.values()),
    }

    # 8b. Fee structure by stage
    fee_structure_by_stage = {}
    fee_structures = FeeStructure.objects.filter(
        term=active_term,
        academic_year=academic_year,
        school=school,
        is_active=True
    )
    for fs in fee_structures:
        fee_structure_by_stage[fs.stage] = {
            'school_fees': float(fs.school_fees),
            'pta_dues': float(fs.pta_dues),
            'computer_levy': float(fs.computer_levy),
            'exam_fees': float(fs.exam_fees),
            'canteen': float(fs.canteen_amount),
            'boarding': float(fs.boarding_fee),
            'hostel': float(fs.hostel_fee),
            'development': float(fs.development_fee),
            'other_fees': float(fs.other_fees),
            'total': float(fs.total_amount),
        }

    # 9. Recent Expenses
    recent_expenses = Expense.objects.filter(school=school).order_by('-expense_date')[:5]

    class_payment_counts = FeePayment.objects.filter(
        recorded_at__date=today
    ).values('fee__student__school_class__name').annotate(total=Count('id'))
    payment_counts = {item['fee__student__school_class__name']: item['total'] for item in class_payment_counts}

    context = {
        'active_term': active_term,
        'current_term': f"Term {active_term.term_number}",
        'current_year': academic_year,
        'total_collected': total_collected,
        'total_outstanding': total_outstanding,
        'total_expected': total_expected,
        'today_collections': today_collections,
        'display_date': display_date,
        'total_expenses': total_expenses,
        'net_balance': total_collected - total_expenses,
        'total_students': total_students,
        'collection_rate': round((total_collected / total_expected * 100), 1) if total_expected > 0 else 0,

        'payments_by_class': payments_by_class,
        'today_payments': today_payments,
        'payment_counts': payment_counts,

        'defaulter_counts': defaulter_counts,
        'defaulter_counts_by_stage': defaulter_counts_by_stage,
        'total_defaulters': total_defaulters,

        'fees_by_stage': fees_by_stage,
        'canteen_by_stage': canteen_by_stage,
        'canteen_totals': canteen_totals,
        'fee_structure_by_stage': fee_structure_by_stage,

        'recent_expenses': recent_expenses,
        'today': today,
        'STAGE_GROUP_MAP': STAGE_GROUP_MAP,
    }

    return render(request, 'accounts/accountant_dashboard.html', context)


@login_required
def record_payment(request, fee_id=None):
    if request.user.role!= 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    search_query = request.GET.get('search', '')

    # Get active term - no fallback
    active_term = Term.objects.filter(school=school, is_active=True).first()
    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one.")
        return redirect('accounts:accountant_dashboard')

    current_term = active_term
    academic_year = active_term.academic_year

    fee = None
    student = None
    search_results = None

    # CASE 1: fee_id provided
    if fee_id:
        fee = get_object_or_404(StudentFee, id=fee_id, student__school=school)
        student = fee.student

    # CASE 2: Search submitted
    elif search_query:
        query = search_query.strip()

        search_results = User.objects.filter(
            school=school,
            role='student',
            is_active=True
        ).filter(
            Q(student_number__icontains=query) |
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(first_name__icontains=query.split(' ')[0]) |
            Q(last_name__icontains=query.split(' ')[-1])
        ).distinct()[:10]

        exact_match = User.objects.filter(
            school=school,
            role='student',
            student_number__iexact=search_query
        ).first()

        if exact_match:
            try:
                fee = StudentFee.objects.get(
                    student=exact_match,
                    term=current_term,
                    academic_year=academic_year
                )
                student = exact_match
                search_results = None
            except StudentFee.DoesNotExist:
                messages.error(request, f"No fee record for {exact_match.get_full_name()} in Term {active_term.term_number}")
                return redirect('accounts:payment_history')

    # Calculate real balance from transactions BEFORE POST check
    real_balance = 0
    real_paid = 0
    if fee:
        # FIXED: Get paid amount from PaymentTransaction, not fee.amount_paid()
        paid_sum = PaymentTransaction.objects.filter(
            student=fee.student,
            term=fee.term,
            academic_year=AcademicYear.objects.get(name=fee.academic_year)
        ).aggregate(total=Sum('total_amount'))['total'] or 0

        real_paid = float(paid_sum)
        real_balance = float(fee.total_amount) - real_paid
        if real_balance < 0:
            real_balance = 0

    # CASE 3: POST - Save payment
    if request.method == 'POST' and fee:
        school_fees = Decimal(request.POST.get('school_fees_paid') or 0)
        canteen = Decimal(request.POST.get('canteen_paid') or 0)
        boarding = Decimal(request.POST.get('boarding_paid') or 0)
        hostel = Decimal(request.POST.get('hostel_paid') or 0)
        development = Decimal(request.POST.get('development_paid') or 0)
        pta = Decimal(request.POST.get('pta_paid') or 0)
        computer = Decimal(request.POST.get('computer_paid') or 0)
        exam = Decimal(request.POST.get('exam_paid') or 0)
        other = Decimal(request.POST.get('other_paid') or 0)

        total_amount = school_fees + canteen + boarding + hostel + development + pta + computer + exam + other

        if total_amount <= 0:
            messages.error(request, "Amount must be greater than 0")
            return redirect('accounts:record_payment', fee_id=fee.id)

        # FIXED: Check against real_balance, not fee.balance()
        if total_amount > real_balance:
            messages.error(request, f"Amount cannot exceed balance of GH₵ {real_balance:.2f}")
            return redirect('accounts:record_payment', fee_id=fee.id)

        try:
            with transaction.atomic():
                # FIXED: total_amount = payment amount, NOT fee.total_amount
                payment_txn = PaymentTransaction.objects.create(
                    term=fee.term,
                    academic_year=AcademicYear.objects.get(name=fee.academic_year),
                    student=fee.student,
                    total_amount=total_amount, 
                    recorded_by=request.user
                )

                # Build payment types list
                payment_types = []
                if school_fees > 0: payment_types.append("School Fees")
                if canteen > 0: payment_types.append("Feeding Fee")
                if boarding > 0: payment_types.append("Boarding Fee")
                if hostel > 0: payment_types.append("Hostel Fee")
                if development > 0: payment_types.append("Development Fee")
                if pta > 0: payment_types.append("PTA Dues")
                if computer > 0: payment_types.append("Computer Levy")
                if exam > 0: payment_types.append("Exam Fees")
                if other > 0: payment_types.append("Other Fees")

                # Add payment types to transaction
                for name in payment_types:
                    code = name.upper().replace(" ", "_")
                    pt, created = PaymentType.objects.get_or_create(
                        code=code,
                        defaults={"name": name}
                    )
                    payment_txn.payment_types.add(pt)

                # Create PaymentItems - DO NOT update StudentFee fields anymore
                allocations = [
                    ('School Fees', school_fees),
                    ('Feeding Fee', canteen),
                    ('Boarding Fee', boarding),
                    ('Hostel Fee', hostel),
                    ('Development Fee', development),
                    ('PTA Dues', pta),
                    ('Computer Levy', computer),
                    ('Exam Fees', exam),
                    ('Other Fees', other),
                ]

                for fee_name, amount_paid in allocations:
                    if amount_paid > 0:
                        PaymentItem.objects.create(
                            transaction=payment_txn,
                            fee_name=fee_name,
                            amount=amount_paid
                        )

                # === NEW: ADMIN MONITORING - Audit Log ===
                FeeAuditLog.objects.create(
                    transaction=payment_txn,
                    action='COLLECTED',
                    done_by=request.user,
                    details=f"Collected GHS {total_amount} for {fee.student.get_full_name()} ({fee.student.student_number}) - {', '.join(payment_types)}"
                )

                messages.success(request, f"Payment of GH₵ {total_amount} recorded. Receipt: {payment_txn.receipt_number}")
                return redirect('accounts:record_payment', fee_id=fee.id)

        except Exception as e:
            messages.error(request, f"Error processing payment: {str(e)}")
            return redirect('accounts:record_payment', fee_id=fee.id)

    # Recalculate total_amount on fee for display
    if fee:
        fee.total_amount = sum([
            fee.school_fees or 0,
            fee.pta_dues or 0,
            fee.computer_levy or 0,
            fee.exam_fees or 0,
            fee.canteen_amount or 0,
            fee.boarding_fee or 0,
            fee.hostel_fee or 0,
            fee.development_fee or 0,
            fee.other_fees or 0
        ])
        fee.save(update_fields=['total_amount'])

    fee_map = {}

    if fee:
        fs = FeeStructure.objects.filter(
            school=school,
            term=fee.term,
            academic_year=fee.academic_year,
            stage=fee.student.school_class.stage,
            is_active=True
        ).first()

        if fs:
            fee_map = {
                "School Fees": fs.school_fees,
                "Feeding": fs.canteen_amount,
                "Boarding": fs.boarding_fee,
                "Hostel": fs.hostel_fee,
                "Development": fs.development_fee,
                "PTA": fs.pta_dues,
                "Computer": fs.computer_levy,
                "Exam": fs.exam_fees,
                "Other": fs.other_fees,
            }
            fee_map = {k: v for k, v in fee_map.items() if v not in [None, 0, ""]}

    context = {
        'fee': fee,
        'student': student,
        'balance': real_balance, # FIXED: Use calculated balance
        'total_paid': real_paid, # ADD THIS for template
        'term': active_term.get_term_number_display(),
        'academic_year': academic_year,
        'active_term': active_term,
        'search_query': search_query,
        'fee_map': fee_map,
        'search_results': search_results,
    }
    return render(request, 'accounts/record_payment.html', context)


@login_required
def add_expense(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES)  
        if form.is_valid():
            expense = form.save(commit=False)
            expense.school = request.user.school
            expense.recorded_by = request.user
            expense.save()
            messages.success(request, f"Expense of GH₵  {expense.amount} added successfully")
            return redirect('accounts:accountant_dashboard')
    else:
        form = ExpenseForm()

    return render(request, 'accounts/add_expense.html', {'form': form})

@login_required
def expense_list(request):
    if request.user.role not in ['admin', 'accountant', 'bursar', 'head']:
        return redirect('accounts:home')

    school = request.user.school
    
    active_term = Term.objects.filter(school=school, is_active=True).first()
    all_terms = Term.objects.filter(school=school).select_related('academic_year').order_by('-academic_year__name', '-term_number')
    
    years = AcademicYear.objects.filter(school=school).order_by('-name')
    
    selected_term_id = request.GET.get('term')
    selected_year = request.GET.get('year') 
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    expenses = Expense.objects.filter(school=school)

    if start_date and end_date:
        expenses = expenses.filter(expense_date__gte=start_date, expense_date__lte=end_date)
        selected_term = None
    elif selected_year and selected_year.isdigit():
        year_obj = years.filter(id=selected_year).first()
        if year_obj:
            terms_in_year = all_terms.filter(academic_year=year_obj)
            if terms_in_year.exists():
                first_term = terms_in_year.order_by('start_date').first()
                last_term = terms_in_year.order_by('-end_date').first()
                expenses = expenses.filter(
                    expense_date__gte=first_term.start_date,
                    expense_date__lte=last_term.end_date
                )
        selected_term = None
    else:
        if selected_term_id and selected_term_id.isdigit():
            selected_term = all_terms.filter(id=selected_term_id).first()
        else:
            selected_term = active_term
        
        if selected_term:
            expenses = expenses.filter(
                expense_date__gte=selected_term.start_date,
                expense_date__lte=selected_term.end_date
            )
        else:
            expenses = Expense.objects.none()

    expenses = expenses.order_by('-expense_date', '-id')
    total_expense = expenses.aggregate(total=Sum('amount'))['total'] or 0
    
    context = {
        'expenses': expenses,
        'active_term': active_term,
        'selected_term': selected_term if 'selected_term' in locals() else None,
        'total_expense': total_expense,
        'all_terms': all_terms,
        'years': years, 
        'selected_term_id': selected_term_id,
        'selected_year_param': selected_year,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'accounts/expense_list.html', context)

@login_required
def verify_expense(request, pk):
    if request.user.role not in ['admin', 'bursar', 'head']:
        return redirect('accounts:home') 
    
    expense = get_object_or_404(Expense, pk=pk, school=request.user.school)
    
    if request.method == 'POST': 
        expense.verified = True
        expense.verified_by = request.user 
        expense.verified_at = timezone.now()  
        expense.save()
        
    return redirect('accounts:expense_list')

@login_required
def edit_expense(request, pk):
    expense = get_object_or_404(Expense, pk=pk, school=request.user.school)
    
    if expense.verified:
        messages.error(request, "Cannot edit a verified expense")
        return redirect('accounts:expense_list')
    
    if expense.recorded_by != request.user:
        messages.error(request, "You can only edit your own expenses")
        return redirect('accounts:expense_list')
    
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, instance=expense)
        if form.is_valid():
            form.save()
            return redirect('accounts:expense_list')
    else:
        form = ExpenseForm(instance=expense)
    
    return render(request, 'accounts/edit_expense.html', {'form': form, 'expense': expense})

@login_required
def delete_expense(request, pk):
    expense = get_object_or_404(Expense, pk=pk, school=request.user.school)
    
    if expense.verified:
        messages.error(request, "Cannot delete a verified expense")
        return redirect('accounts:expense_list')
    
    if expense.created_by != request.user:
        messages.error(request, "You can only delete your own expenses")
        return redirect('accounts:expense_list')
    
    if request.method == 'POST':
        expense.delete()
        messages.success(request, "Expense deleted")
        return redirect('accounts:expense_list')
    
    return render(request, 'accounts/confirm_delete.html', {'expense': expense})


@login_required
def student_fees_list(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school

    # Get active term for this school only
    active_term = Term.objects.filter(school=school, is_active=True).first()
    
    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one in Term Settings.")
        return render(request, 'accounts/student_fees_list.html', {
            'class_data': [],
            'active_term': None,
            'total_defaulters': 0
        })

    academic_year = active_term.academic_year

    classes = SchoolClass.objects.filter(school=school)
    class_data = []
    total_defaulters = 0

    for school_class in classes:
        students = User.objects.filter(
            school_class=school_class,
            role='student',
            is_active=True
        )

        if not students.exists():
            continue

        students_data = []
        for student in students:
            try:
                fee = StudentFee.objects.get(
                    student=student, 
                    term_id=active_term.id,
                    academic_year=academic_year
                )
                balance = fee.balance()
                if balance > 0:
                    paid = fee.amount_paid()
                    total = fee.total_amount
                    students_data.append({
                        'student': student,
                        'total': total,
                        'paid': paid,
                        'balance': balance,
                        'fee_id': fee.id
                    })
            except StudentFee.DoesNotExist:
                continue

        if students_data:
            class_data.append({
                'class_name': school_class.name,
                'students': students_data,
                'count': len(students_data)
            })
            total_defaulters += len(students_data)

    context = {
        'class_data': class_data,
        'active_term': active_term,
        'current_term': f"Term {active_term.term_number}",
        'current_year': academic_year,
        'total_defaulters': total_defaulters
        
    }
    return render(request, 'accounts/student_fees_list.html', context)

@login_required
def set_student_fee(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    students = User.objects.filter(school=school, role='student', is_active=True).order_by('first_name')
    classes = SchoolClass.objects.filter(school=school)

    try:
        active_term = Term.objects.get(school=school, is_active=True)
        academic_year = active_term.academic_year
    except Term.DoesNotExist:
        messages.error(request, "No active term set. Ask admin to set one.")
        return redirect('accounts:accountant_dashboard')

    if request.method == 'POST':
        # Bulk set by class
        if 'bulk_set' in request.POST:
            class_id = request.POST.get('school_class')
            term_id = request.POST.get('term')
            term = Term.objects.get(id=term_id)
            year_id = request.POST.get('academic_year')
            academic_year_obj = AcademicYear.objects.get(id=year_id)
            due_date = request.POST.get('due_date')

            school_fees = request.POST.get('school_fees') or 0
            pta_dues = request.POST.get('pta_dues') or 0
            computer_levy = request.POST.get('computer_levy') or 0
            exam_fees = request.POST.get('exam_fees') or 0
            other_fees = request.POST.get('other_fees') or 0

            students_in_class = User.objects.filter(
                school=school, role='student',
                school_class_id=class_id, is_active=True
            )
            for student in students_in_class:
                StudentFee.objects.update_or_create(
                    student=student, term=term, academic_year=academic_year_obj,
                    defaults={
                        'school_fees': school_fees,
                        'pta_dues': pta_dues,
                        'computer_levy': computer_levy,
                        'exam_fees': exam_fees,
                        'other_fees': other_fees,
                        'due_date': due_date
                    }
                )
            messages.success(request, f"Fees set for {students_in_class.count()} students in class")
            return redirect('accounts:set_student_fee')
        # Single student set
        else:
            student_id = request.POST.get('student')
            term_id = request.POST.get('term')
            term = Term.objects.get(id=term_id)
            year_id = request.POST.get('academic_year')
            academic_year_obj = AcademicYear.objects.get(id=year_id)
            due_date = request.POST.get('due_date')

            school_fees = request.POST.get('school_fees') or 0
            pta_dues = request.POST.get('pta_dues') or 0
            computer_levy = request.POST.get('computer_levy') or 0
            exam_fees = request.POST.get('exam_fees') or 0
            other_fees = request.POST.get('other_fees') or 0

            student = get_object_or_404(User, id=student_id, school=school)
            StudentFee.objects.update_or_create(
                student=student, term=term, academic_year=academic_year_obj,
                defaults={
                    'school_fees': school_fees,
                    'pta_dues': pta_dues,
                    'computer_levy': computer_levy,
                    'exam_fees': exam_fees,
                    'other_fees': other_fees,
                    'due_date': due_date
                }
            )
            messages.success(request, f"Fee set for {student.get_full_name()}")
            return redirect('accounts:set_student_fee')

    # GET request
    fee_structure_by_stage = {}
    fee_structures = FeeStructure.objects.filter(
        term=active_term,
        academic_year=academic_year,
        school=school,
        is_active=True,
        is_published=True
    ).order_by('stage')

    for fs in fee_structures:
        data = {"stage": fs.stage, "total": float(fs.total_amount)}
        if fs.school_fees not in [None, 0, ""]:
            data["school_fees"] = float(fs.school_fees)
        if fs.canteen_amount not in [None, 0, ""]:
            data["canteen"] = float(fs.canteen_amount)
        if fs.boarding_fee not in [None, 0, ""]:
            data["boarding_fee"] = float(fs.boarding_fee)
        if fs.hostel_fee not in [None, 0, ""]:
            data["hostel_fee"] = float(fs.hostel_fee)
        if fs.exam_fees not in [None, 0, ""]:
            data["exam_fees"] = float(fs.exam_fees)
        if fs.development_fee not in [None, 0, ""]:
            data["development_fee"] = float(fs.development_fee)
        if fs.pta_dues not in [None, 0, ""]:
            data["pta_dues"] = float(fs.pta_dues)
        if fs.computer_levy not in [None, 0, ""]:
            data["computer_levy"] = float(fs.computer_levy)
        if fs.other_fees not in [None, 0, ""]:
            data["other_fees"] = float(fs.other_fees)
        fee_structure_by_stage[fs.stage] = data

    # context is OUTSIDE the for loop now
    context = {
        'students': students,
        'classes': classes,
        'term': getattr(active_term, "name", str(active_term)),
        'academic_year': str(active_term.academic_year),
        'fee_structure_by_stage': fee_structure_by_stage,
    }
    return render(request, 'accounts/set_student_fee.html', context)


@login_required
def all_students_fees_list(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school

    active_term = Term.objects.filter(school=school, is_active=True).first()
    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one in Term Settings.")
        return render(request, 'accounts/all_students_fees_list.html', {
            'class_data': [],
            'total_owing': 0,
            'total_defaulters': 0,
            'active_term': None,
            'generated_on': date.today(),
        })

    current_term = active_term
    academic_year = active_term.academic_year

    classes = SchoolClass.objects.filter(school=school).prefetch_related(
        Prefetch(
            'students',
            queryset=User.objects.filter(role='student', is_active=True).select_related('school_class'),
            to_attr='students_list'
        )
    )
    
    class_data = []
    total_owing = 0
    total_defaulters = 0
    generated_on = date.today()

    # Get all payments for this term in one query
    all_payments = PaymentTransaction.objects.filter(
        student__school=school,
        term=active_term,
        academic_year=academic_year
    ).values('student_id').annotate(total_paid=Sum('total_amount'))
    
    # Build map: student_id -> total_paid
    student_paid_map = {p['student_id']: float(p['total_paid'] or 0) for p in all_payments}

    for school_class in classes:
        students = school_class.students_list
        if not students:
            continue

        student_ids = [s.id for s in students]
        fees = StudentFee.objects.filter(
            student_id__in=student_ids,
            term_id=active_term.id,
            academic_year=academic_year
        ).select_related('student')

        fees_by_student = {f.student_id: f for f in fees}

        students_data = []
        class_total = 0

        for student in students:
            fee = fees_by_student.get(student.id)
            if not fee:
                continue

            # FIXED: Calculate from PaymentTransaction, not fee.balance()
            paid = student_paid_map.get(student.id, 0)
            balance = float(fee.total_amount) - paid

            if balance > 0:
                students_data.append({
                    'student': student,
                    'student_number': student.student_number,
                    'total': fee.total_amount,
                    'paid': paid,  # Use real sum from transactions
                    'balance': balance,  # Calculated balance
                    'fee_id': fee.id,
                    'status': 'Owing'
                })
                total_owing += balance
                class_total += balance
                total_defaulters += 1

        if students_data:
            class_data.append({
                'class_name': school_class.name,
                'students': students_data,
                'class_id': school_class.id,
                'count': len(students_data),
                'total_owing': class_total
            })

    context = {
        'class_data': class_data,
        'current_term': active_term.get_term_number_display(),
        'current_year': academic_year,
        'active_term': active_term,
        'total_owing': total_owing,
        'total_defaulters': total_defaulters,
        'page_title': 'Fee Defaulters Report',
        'generated_on': generated_on,
    }
    return render(request, 'accounts/all_students_fees_list.html', context)


@login_required
def payment_history(request):
    
    if request.user.role!= 'accountant':
        return redirect('accounts:home')

    school = request.user.school

    transactions = PaymentTransaction.objects.filter(
        student__school=school
    ).select_related(
        'student',
        'recorded_by',
        'student__school_class'
    ).prefetch_related('payment_types').order_by('-created_at') # keep it on the same chain

    if not transactions:
        return render(request, 'accounts/payment_history.html', {
            'transactions': [],
            'grouped_transactions': {},
        })

    # Build lookup for StudentFee objects
    student_ids = set(tx.student_id for tx in transactions)
    fee_keys = {(tx.term, tx.academic_year, tx.student_id) for tx in transactions}

    fees = StudentFee.objects.filter(
        student_id__in=student_ids,
        term__in={k[0] for k in fee_keys},
        academic_year__in={k[1] for k in fee_keys}
    )

    fees_map = {(f.term, f.academic_year, f.student_id): f for f in fees}

    grouped = defaultdict(list)

    for tx in transactions:
        key = (tx.term, str(tx.academic_year), tx.student_id)
        fee = fees_map.get(key)

        tx.student_fee = fee
        tx.payment_list = [pt.name for pt in tx.payment_types.all()]

        # ADD THIS
        if fee:
            tx.fee_balance = fee.balance()
        else:
            tx.fee_balance = 0

        class_name = tx.student.school_class.name if tx.student.school_class else "Unassigned"
        grouped[class_name].append(tx)

    return render(request, 'accounts/payment_history.html', {
        'transactions': transactions,
        'grouped_transactions': dict(grouped),
        'active_term': Term.objects.filter(school=school, is_active=True).first()
    })


@login_required
def view_receipt(request, payment_id):
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related('student', 'recorded_by'),
        id=payment_id
    )

    # Get fee breakdown from StudentFee instead of payment_type
    try:
        student_fee = StudentFee.objects.get(
            student=payment.student,
            term=payment.term,
            academic_year=payment.academic_year
        )
        payment_types = [p.fee_type.name for p in student_fee.payments.all()]
    except StudentFee.DoesNotExist:
        payment_types = []

    context = {
        'payment': payment,
        'school': payment.student.school,
        'accountant': payment.recorded_by,
        'payment_types': payment_types,
        'total_paid': payment.total_amount,
    }

    return render(request, 'accounts/receipt.html', context)

@login_required
def verify_payment(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    query = request.GET.get('query', '')

    receipt = None
    student = None
    payments = None

    if query:
        # 1. Try receipt search first
        receipt = PaymentTransaction.objects.filter(
            receipt_number__iexact=query,
            student__school=school
        ).select_related('student', 'student__school_class').first()

        # 2. If receipt found
        if receipt:
            student = receipt.student
            payments = PaymentTransaction.objects.filter(
                student=student,
                term=receipt.term,
                academic_year=receipt.academic_year
            ).order_by('-created_at')

        else:
            # 3. Student search fallback
            student = User.objects.filter(
                school=school,
                role='student',
                is_active=True
            ).filter(
                Q(student_number__iexact=query) |
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query)
            ).first()

            if student:
                payments = PaymentTransaction.objects.filter(
                    student=student
                ).order_by('-created_at')

    context = {
        'query': query,
        'receipt': receipt,
        'student': student,
        'payments': payments,
    }

    return render(request, 'accounts/verify_payment.html', context)


@login_required
def generate_report(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')
    
    school = request.user.school
    today = date.today()
    
    # NEW: Get dates from URL if they exist
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')
    
    # CHANGED: Wrap your old logic in an if/else
    if start_date and end_date:
        # NEW: User picked dates, so use those
        from datetime import datetime
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        date_range_text = f"{start} to {end}"
        
        # NEW: Filter by date range instead of term
        total_collected = PaymentTransaction.objects.filter(
            student__school=school,
            created_at__date__range=[start, end]
        ).aggregate(
            total=Sum('amount_paid')
        )['total'] or 0
                
        total_expenses = Expense.objects.filter(
            school=school,
            expense_date__range=[start, end]
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        transactions = PaymentTransaction.objects.filter(
            student__school=school,
            created_at__date__range=[start, end]
        ).select_related(
            'student',
            'student__school_class',
            'term',
            'academic_year'
        ).order_by('-created_at')
        
        expenses = Expense.objects.filter(
            school=school,
            expense_date__range=[start, end]
        ).order_by('-expense_date')[:20]
        
        # NEW: Defaulters don't make sense for date range, so empty list
        defaulters = []
        current_term = 'Custom Range'
        academic_year = ''
        
    else:
        # YOUR OLD CODE: This runs if no dates were picked
        try:
            active_term = Term.objects.get(school=school, is_active=True)
            current_term = active_term
            academic_year = active_term.academic_year
        except Term.DoesNotExist:
            messages.error(request, "No active term set.")
            return redirect('accounts:accountant_dashboard')
        if active_term.term_number == 1:
            term_name = "First Term"
        elif active_term.term_number == 2:
            term_name = "Second Term"
        elif active_term.term_number == 3:
            term_name = "Third Term"
        else:
            term_name = f"Term {active_term.term_number}"

        date_range_text = f"{term_name} {academic_year}"
        
        # Your existing queries
        total_collected = PaymentTransaction.objects.filter(
            student__school=school,
            term=current_term,
            academic_year=academic_year
        ).aggregate(
            total=Sum('amount_paid')
        )['total'] or 0
                        
        total_expenses = Expense.objects.filter(
            school=school,
            expense_date__year=active_term.start_date.year if 'active_term' in locals() else today.year
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        transactions = PaymentTransaction.objects.filter(
            student__school=school,
            term=current_term
        ).select_related(
            'student',
            'student__school_class',
            'term',
            'academic_year'
        ).order_by('-created_at')
        expenses = Expense.objects.filter(school=school).order_by('-expense_date')[:20]
        
        # Get defaulters for current term
    defaulters = []

    if start_date and end_date:
        # Date range mode → still calculate ALL student fees (not term-based)
        all_fees = StudentFee.objects.filter(
            student__school=school
        ).select_related('student', 'student__school_class')

    else:
        # Term mode → correct filtering
        all_fees = StudentFee.objects.filter(
            term=current_term,
            academic_year=academic_year,
            student__school=school
        ).select_related('student', 'student__school_class')


    for fee in all_fees:
        balance = fee.balance()
        if balance > 0:
            defaulters.append({
                'student': fee.student,
                'class_name': fee.student.school_class.name if fee.student.school_class else "No Class",
                'total': fee.total_amount,
                'paid': fee.amount_paid(),
                'balance': balance
            })

    defaulters.sort(key=lambda x: (x['class_name'], x['student'].first_name))
    
    template = get_template('accounts/report_pdf.html')
    context = {
        'school': school,
        'date_range': date_range_text, # NEW: Use this in template
        'term': current_term,
        'academic_year': academic_year,
        'total_collected': total_collected,
        'total_expenses': total_expenses,
        'net_balance': total_collected - total_expenses,
        'payments': transactions,
        'expenses': expenses,
        'generated_date': today,
        'defaulters': defaulters,
        'total_defaulters': len(defaulters),
        'school_logo': request.build_absolute_uri(school.logo.url) if school.logo else None,
    }
    html = template.render(context)
    
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
    
    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        # CHANGED: Use date_range in filename
        filename = f"Financial_Report_{date_range_text.replace(' ', '_').replace('/', '-')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    
    return HttpResponse('Error generating PDF', status=500)


@login_required
def report_filters(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')
    
    if request.method == 'POST':
        start = request.POST.get('start_date')
        end = request.POST.get('end_date')
        format_type = request.POST.get('format')
        
        if format_type == 'excel':
            return redirect(f"{reverse('accounts:generate_report_excel')}?start={start}&end={end}")
        return redirect(f"{reverse('accounts:generate_report')}?start={start}&end={end}")
    
    return render(request, 'accounts/report_filters.html', {
        'today': date.today(),  # ← send date object for top bar
        'today_str': date.today().strftime('%Y-%m-%d')  # ← use this for your input default
    })

@login_required
def generate_report_excel(request):
    from openpyxl.styles import Font, Alignment, Border, Side

    header_font = Font(bold=True)

    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    if request.user.role!= 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    today = date.today()

    # Copy the EXACT same date logic from generate_report
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')

    if start_date and end_date:
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        date_range_text = f"{start} to {end}"

        if start_date and end_date:
            payments = PaymentTransaction.objects.filter(
                student__school=school,
                created_at__date__range=[start, end]
            ).select_related('student', 'student__school_class')

        else:
            payments = PaymentTransaction.objects.filter(
                student__school=school
            ).select_related('student', 'student__school_class')

        if start_date and end_date:
            expenses = Expense.objects.filter(
                school=school,
                expense_date__range=[start, end]
            ).order_by('-expense_date')
        else:
            expenses = Expense.objects.filter(
                school=school
            ).order_by('-expense_date')[:20]
        defaulters = []
    else:
        try:
            active_term = Term.objects.get(school=school, is_active=True)
            current_term = active_term
            academic_year = active_term.academic_year
        except Term.DoesNotExist:
            messages.error(request, "No active term set.")
            return redirect('accounts:accountant_dashboard')
        date_range_text = f"Term {active_term.term_number} {academic_year}"
        if start_date and end_date:
            payments = PaymentTransaction.objects.filter(
                student__school=school,
                created_at__date__range=[start, end]
            ).select_related('student', 'student__school_class')

        else:
            payments = PaymentTransaction.objects.filter(
                student__school=school
            ).select_related('student', 'student__school_class')

        expenses = Expense.objects.filter(school=school).order_by('-expense_date')[:20]

        # Get defaulters
        defaulters = []
        all_fees = StudentFee.objects.filter(
            term=current_term,
            academic_year=academic_year,
            student__school=school
        ).select_related('student', 'student__school_class')
        
        for fee in all_fees:
            balance = fee.balance()
            if balance > 0:
                defaulters.append({
                    'student': f"{fee.student.first_name} {fee.student.last_name}",
                    'class_name': fee.student.school_class.name if fee.student.school_class else "No Class",
                    'total': float(fee.total_amount),
                    'paid': float(fee.amount_paid()),
                    'balance': float(balance)
                })

    # Create Excel
    wb = openpyxl.Workbook()

    # Sheet 1: Summary
    ws1 = wb.active
    ws1.title = "Summary"
    ws1.append([school.name])
    ws1.append([f"Financial Report - {date_range_text}"])
    ws1.append([f"Generated: {today}"])

    ws1.append([])

    ws1.append(["TOTAL COLLECTED", f"GH₵  {sum(p.amount_paid for p in payments):,.2f}"])
    ws1.append(["TOTAL EXPENSES", f"GH₵  {sum(e.amount for e in expenses):,.2f}"])
    ws1.append(["NET BALANCE", f"GH₵  {(sum(p.amount_paid for p in payments) - sum(e.amount for e in expenses)):,.2f}"])
    # make labels bold
    for row in ws1.iter_rows(min_row=5, max_row=8, min_col=1, max_col=2):
        for cell in row:
            cell.border = thin_border
            if cell.column == 1:
                cell.font = Font(bold=True)
        for col in ws1.columns:
            max_length = 0
            column = col[0].column_letter

            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass

            ws1.column_dimensions[column].width = max_length + 5

    # Sheet 2: Payments
    ws2 = wb.create_sheet("Payments")
    ws2.append(["Date", "Student", "Class", "Amount"])

    for cell in ws2[1]:
        cell.font = header_font
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center")
    for p in payments:
        ws2.append([
            p.created_at.strftime('%Y-%m-%d'),
            f"{p.student.first_name} {p.student.last_name}",
            p.student.school_class.name if p.student.school_class else "No Class",
            float(p.amount_paid)
        ])

        # ADD BORDER TO THE ROW YOU JUST ADDED
        for cell in ws2[ws2.max_row]:
            cell.border = thin_border
    # Sheet 3: Expenses
    ws3 = wb.create_sheet("Expenses")
    ws3.append(["Date", "Description", "Amount"])

    for cell in ws3[1]:
        cell.font = header_font
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center")
    for e in expenses:
        ws3.append([
            e.expense_date.strftime('%Y-%m-%d'),
            e.description,
            float(e.amount)
        ])

        for cell in ws3[ws3.max_row]:
            cell.border = thin_border

    # Sheet 4: Defaulters
    if defaulters:
        ws4 = wb.create_sheet("Defaulters")
        ws4.append(["Student", "Class", "Total Fee", "Paid", "Balance"])
        for d in defaulters:
            ws4.append([d['student'], d['class_name'], d['total'], d['paid'], d['balance']])

    # Format headers
    for ws in [ws2, ws3]:
        for cell in ws[1]:
            cell.font = Font(bold=True)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f"Financial_Report_{date_range_text.replace(' ', '_').replace('/', '-')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response

def auto_fit(ws):
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter

        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))

        ws.column_dimensions[column].width = max_length + 5


    auto_fit(ws1)
    auto_fit(ws2)
    auto_fit(ws3)

    if defaulters:
        auto_fit(ws4)


def render_to_pdf(template_src, context_dict={}):
    template = get_template(template_src)
    html = template.render(context_dict)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
    if not pdf.err:
        return HttpResponse(result.getvalue(), content_type='application/pdf')
    return None

@login_required
def download_defaulters_pdf(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    try:
        active_term = Term.objects.get(school=school, is_active=True)
        current_term = active_term
        academic_year = active_term.academic_year
    except Term.DoesNotExist:
        messages.error(request, "No active term set.")
        return redirect('accounts:accountant_dashboard')
    date_range_text = f"Term {active_term.term_number} {academic_year}"

    classes = SchoolClass.objects.filter(school=school)
    class_data = []
    total_owing = 0
    total_defaulters = 0

    for school_class in classes:
        students = User.objects.filter(
            school_class=school_class,
            role='student',
            is_active=True
        )
        students_data = []
        for student in students:
            try:
                fee = StudentFee.objects.get(
                    student=student, 
                    term=current_term, 
                    academic_year=academic_year
                )
                balance = fee.balance()
                if balance > 0:
                    students_data.append({
                        'student': student,
                        'student_number': student.student_number,
                        'total': fee.total_amount,
                        'paid': fee.amount_paid(),
                        'balance': balance,
                    })
                    total_owing += balance
                    total_defaulters += 1
            except StudentFee.DoesNotExist:
                continue

        if students_data:
            class_data.append({
                'class_name': school_class.name,
                'students': students_data,
                'count': len(students_data)
            })

    context = {
        'class_data': class_data,
        'current_term': current_term.replace('term', 'Term '),
        'current_year': academic_year,
        'total_owing': total_owing,
        'total_defaulters': total_defaulters,
        'school': school,
        'date_generated': date.today(),
    }
    
    pdf = render_to_pdf('accounts/pdf_defaulters_report.html', context)
    if pdf:
        response = HttpResponse(pdf, content_type='application/pdf')
        filename = f"Fee_Defaulters_{academic_year}_{current_term}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    return HttpResponse("PDF generation failed")


@login_required
def download_class_defaulters_pdf(request, class_id):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    school_class = get_object_or_404(SchoolClass, id=class_id, school=school)
    
    try:
        active_term = Term.objects.get(school=school, is_active=True)
        current_term = active_term
        academic_year = active_term.academic_year
    except Term.DoesNotExist:
        messages.error(request, "No active term set.")
        return redirect('accounts:accountant_dashboard')
    date_range_text = f"Term {active_term.term_number} {academic_year}"

    students = User.objects.filter(
        school_class=school_class,
        role='student',
        is_active=True
    )
    
    students_data = []
    class_total_owing = 0
    
    for student in students:
        try:
            fee = StudentFee.objects.get(
                student=student, 
                term=current_term, 
                academic_year=academic_year
            )
            balance = fee.balance()
            if balance > 0:
                students_data.append({
                    'student': student,
                    'student_number': student.student_number,
                    'total': fee.total_amount,
                    'paid': fee.amount_paid(),
                    'balance': balance,
                })
                class_total_owing += balance
        except StudentFee.DoesNotExist:
            continue

    context = {
        'class_name': school_class.name,
        'students': students_data,
        'count': len(students_data),
        'class_total_owing': class_total_owing,
        'current_term': current_term.replace('term', 'Term '),
        'current_year': academic_year,
        'school': school,
        'date_generated': date.today(),
    }
    
    pdf = render_to_pdf('accounts/pdf_class_defaulters.html', context)
    if pdf:
        response = HttpResponse(pdf, content_type='application/pdf')
        filename = f"Defaulters_{school_class.name}_{academic_year}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    return HttpResponse("PDF generation failed")


@login_required
def apply_fee_structure(request):
    if request.user.role != 'accountant':
        return redirect('accounts:home')

    school = request.user.school
    fee_structures = FeeStructure.objects.filter(school=school, is_active=True)
    
    STAGE_GROUP_MAP = {
        'creche': ['creche'],
        'nursery': ['nursery'],
        'kg': ['kg1', 'kg2'],
        'lower_primary': ['primary1', 'primary2', 'primary3'],
        'upper_primary': ['primary4', 'primary5', 'primary6'],
        'jhs': ['jhs1', 'jhs2', 'jhs3'],
        'shs': ['shs1', 'shs2', 'shs3'],
    }

    if request.method == 'POST':
        structure_id = request.POST.get('fee_structure')
        stage_group = request.POST.get('stage_group')  # CHANGED from school_class
        due_date = request.POST.get('due_date')

        structure = get_object_or_404(FeeStructure, id=structure_id, school=school)
        
        student_stages = STAGE_GROUP_MAP.get(stage_group, [])
        students = User.objects.filter(
            school=school, 
            stage__in=student_stages, 
            role='student', 
            is_active=True
        )

        created = 0
        updated = 0
        for student in students:
            obj, created_flag = StudentFee.objects.update_or_create(
                student=student,
                term=structure.term,
                academic_year=structure.academic_year,
                defaults={
                    'school_fees': structure.school_fees,
                    'pta_dues': structure.pta_dues,
                    'boarding_fee': structure.boarding_fee,
                    'hostel_fee': structure.hostel_fee,
                    'development_fee': structure.development_fee,
                    'computer_levy': structure.computer_levy,
                    'canteen_amount': structure.canteen_amount,
                    'exam_fees': structure.exam_fees,
                    'other_fees': structure.other_fees,
                    'total_amount': structure.total_amount,
                    'due_date': due_date
                }
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        messages.success(request, f"Applied {structure} to {created} new, {updated} updated students in {stage_group}")
        return redirect('accounts:apply_fee_structure')

    context = {
        'fee_structures': fee_structures,
        'stage_groups': FeeStructure.STAGE_CHOICES,  
    }
    return render(request, 'accounts/apply_fee_structure.html', context)


from django.urls import reverse


@login_required
def fee_structure_settings(request):
    if request.user.role != 'admin':
        messages.error(request, "Only school owners can access this page.")
        return redirect('accounts:dashboard')

    school = request.user.school

    years = FeeStructure.objects.filter(school=school).values_list('academic_year', flat=True).distinct().order_by('-academic_year')

    selected_year = request.GET.get('year')
    if not selected_year:
        active_term = Term.objects.filter(is_active=True, school=school).first()
        selected_year = active_term.academic_year if active_term else (years.first() if years else '')

    fee_structures = FeeStructure.objects.filter(school=school, academic_year=selected_year).order_by('stage', 'term')

    if request.method == 'POST':
        stage = request.POST.get('stage')
        term_id = request.POST.get('term')
        term = Term.objects.filter(term_number=term_id, school=school).first()

        if not term:
            messages.error(request, "Term not found for this school.")
            return redirect('accounts:fee_structure_settings')

        academic_year = request.POST.get('academic_year')
        due_date = request.POST.get('due_date') or None

        def to_decimal(val):
            try:
                return Decimal(val) if val not in [None, ''] else Decimal('0')
            except:
                return Decimal('0')

        school_fees = to_decimal(request.POST.get('school_fees'))
        boarding_fee = to_decimal(request.POST.get('boarding_fee'))
        hostel_fee = to_decimal(request.POST.get('hostel_fee'))
        development_fee = to_decimal(request.POST.get('development_fee'))
        pta_dues = to_decimal(request.POST.get('pta_dues'))
        computer_levy = to_decimal(request.POST.get('computer_levy'))
        exam_fees = to_decimal(request.POST.get('exam_fees'))
        other_fees = to_decimal(request.POST.get('other_fees'))
        canteen_amount = to_decimal(request.POST.get('canteen_amount'))
        canteen_type = request.POST.get('canteen_type', 'terminal')

        total = school_fees + pta_dues + computer_levy + exam_fees + other_fees + canteen_amount + boarding_fee + hostel_fee + development_fee

        obj, created = FeeStructure.objects.update_or_create(
            school=school, 
            term=term,
            stage=stage, 
            academic_year=academic_year,
            defaults={
                'school_fees': school_fees,
                'boarding_fee': boarding_fee,
                'hostel_fee': hostel_fee,
                'development_fee': development_fee,
                'pta_dues': pta_dues,
                'computer_levy': computer_levy,
                'exam_fees': exam_fees,
                'other_fees': other_fees,
                'canteen_amount': canteen_amount,
                'canteen_type': canteen_type,
                'due_date': due_date,
                'total_amount': total,
                'is_published': False,
                'is_active': True
            }
        )

        if created:
            messages.success(request, f"Fee structure for {obj.get_stage_display()} - Term {obj.term.term_number} {academic_year} created.")
        else:
            messages.success(request, f"Fee structure for {obj.get_stage_display()} - Term {obj.term.term_number} {academic_year} updated.")

        request.session['last_fee_stage'] = stage
        request.session['last_fee_term'] = term.id
        request.session['last_fee_year'] = academic_year

        return redirect(f"{reverse('accounts:fee_structure_settings')}?stage={stage}&term={term.id}&year={academic_year}")

    selected_stage = request.GET.get('stage')
    selected_term_id = request.GET.get('term')
    selected_year_param = request.GET.get('year') or selected_year

    fee_breakdown = None
    if selected_stage and selected_term_id and selected_year_param:
        fee_breakdown = FeeStructure.objects.filter(
            school=school,
            stage=selected_stage,
            term_id=selected_term_id,
            academic_year=selected_year_param,
            is_active=True
        ).first()
        if fee_breakdown:
            request.session['last_fee_stage'] = selected_stage
            request.session['last_fee_term'] = selected_term_id
            request.session['last_fee_year'] = selected_year_param

    if not fee_breakdown:
        last_stage = request.session.get('last_fee_stage')
        last_term = request.session.get('last_fee_term')
        last_year = request.session.get('last_fee_year')
        if last_stage and last_term and last_year:
            fee_breakdown = FeeStructure.objects.filter(
                school=school,
                stage=last_stage,
                term_id=last_term,
                academic_year=last_year,
                is_active=True
            ).first()

        if not fee_breakdown:
            fee_breakdown = FeeStructure.objects.filter(
                school=school, is_active=True
            ).order_by('-id').first()

    return render(request, 'accounts/fee_structure_settings.html', {
        'school': school,
        'fee_structures': fee_structures,
        'years': years,
        'selected_year': selected_year,
        'stage_choices': FeeStructure.STAGE_CHOICES,
        'fee_breakdown': fee_breakdown,
    })


@login_required
@user_passes_test(is_admin)
def generate_fees_for_term(request):
    school = request.user.school
    
    if request.method == 'POST':
        stage_group = request.POST.get('stage_group')  
        academic_year_str = request.POST.get('academic_year')  # "2025/2026"
        
        if not academic_year_str:  # fix variable name
            messages.error(request, "Invalid academic year.")
            return redirect('accounts:fee_structure_settings')  # STAY ON SAME PAGE
        
        term_id = request.POST.get('term')


        term_obj = Term.objects.filter(
            school=school,
            id=term_id,
        ).first()

        if not term_obj:
            messages.error(request, "Term not found.")
            return redirect('accounts:fee_structure_settings')  # STAY ON SAME PAGE
        
        STAGE_GROUP_MAP = {
            'creche': ['Creche'],
            'nursery': ['Nursery 1', 'Nursery 2'], 
            'kg': ['KG 1', 'KG 2'],
            'lower_primary': ['Primary 1', 'Primary 2', 'Primary 3'],
            'upper_primary': ['Primary 4', 'Primary 5', 'Primary 6'],
            'jhs': ['JHS 1', 'JHS 2', 'JHS 3'],
            'shs': ['SHS 1', 'SHS 2', 'SHS 3'],
        }  
        
        student_stages = STAGE_GROUP_MAP.get(stage_group, [])
        if not student_stages:
            messages.error(request, "Invalid stage selected.")
            return redirect('accounts:fee_structure_settings')  
        
        # Get fee structure for the group
        fee_structure = FeeStructure.objects.filter(
            school=school, 
            stage=stage_group, 
            term=term_obj,
            academic_year=academic_year_str,
            is_active=True
        ).first()  # add .first()
        
        if not fee_structure:
            messages.error(request, f"No fee structure set for {stage_group} {academic_year_str}.")
            return redirect('accounts:fee_structure_settings') 
        # Publish it
        fee_structure.is_published = True
        fee_structure.save(update_fields=['is_published'])

        # Get students in those classes
        students = User.objects.filter(
            school=school,
            school_class__stage=stage_group,
            role__iexact='student',
            is_active=True
        ).select_related('school_class')
                
        if not students.exists():
            messages.warning(request, f"No students found in {', '.join(student_stages)}.")
            return redirect('accounts:fee_structure_settings')  # STAY ON SAME PAGE
        updated = 0
        created = 0
        
        for student in students:
            obj, created_flag = StudentFee.objects.update_or_create(
                student=student,
                school=school,
                term=term_obj,  
                academic_year=academic_year_str, 
                defaults={
                    'school': school,
                    'school_fees': fee_structure.school_fees,
                    'canteen_amount': fee_structure.canteen_amount,
                    'pta_dues': fee_structure.pta_dues,
                    'computer_levy': fee_structure.computer_levy,
                    'exam_fees': fee_structure.exam_fees,
                    'other_fees': fee_structure.other_fees,
                    'development_fee': fee_structure.development_fee,
                    'boarding_fee': fee_structure.boarding_fee,
                    'hostel_fee': fee_structure.hostel_fee,
                    'due_date': fee_structure.due_date,
                    'total_amount': (
                        fee_structure.school_fees +
                        fee_structure.canteen_amount +
                        fee_structure.pta_dues +
                        fee_structure.development_fee +
                        fee_structure.boarding_fee +
                        fee_structure.hostel_fee +
                        fee_structure.computer_levy +
                        fee_structure.exam_fees +
                        fee_structure.other_fees
                    )
                }
            )
            if created_flag:
                created += 1
            else:
                updated += 1
        
        messages.success(
            request, 
            f"Done. Created {created} new fees, updated {updated} existing fees for {stage_group} - Term {term_obj} - {academic_year_str}"
        )
        return redirect('accounts:fee_structure_settings')    
    return redirect('accounts:fee_structure_settings')  


def is_admin(user):
    return user.is_superuser or user.is_staff

@login_required
def student_fees_list(request):
    school = request.user.school  
    
    term_id = request.GET.get('term', '')  
    year_id = request.GET.get('year')
    search = request.GET.get('search', '')
    new_only = request.GET.get('new_only') == 'on'
    
    terms = Term.objects.filter(school=school).order_by('term_number')  
    year_choices = AcademicYear.objects.filter(school=school).order_by('-name')
    
    fees = StudentFee.objects.filter(school=school).select_related('student', 'term')
    selected_year = year_choices.filter(id=year_id).first()

    if selected_year:
        fees = fees.filter(academic_year=selected_year.name)
    
    if term_id and term_id.isdigit():
        fees = fees.filter(term_id=term_id)
    
    current_year = AcademicYear.objects.filter(school=school).last()
    current_term = Term.objects.filter(school=school, is_active=True).first()

    if not year_id and current_year:
        year_id = str(current_year.id)
    if not term_id and current_term:
        term_id = str(current_term.id)
    
    if search:
        fees = fees.filter(
            Q(student__first_name__icontains=search) |
            Q(student__last_name__icontains=search) |
            Q(student__student_number__icontains=search)
        )
    
    selected_term_id = term_id if term_id and term_id.isdigit() else ''
    selected_term_obj = terms.filter(id=selected_term_id).first() if selected_term_id else None
    
    selected_year = year_choices.filter(id=year_id).first()
    
    context = {
        'fees': fees,
        'selected_year': selected_year,
        'terms': terms,
        'year_choices': year_choices,
        'selected_term': selected_term_id,
        'term_label': str(selected_term_obj) if selected_term_obj else "All Terms", 
        'search': search,
        'new_only': new_only,
    }
    return render(request, 'accounts/student_fees_list.html', context)

@login_required
def edit_student_fee(request, fee_id):
    school = request.user.school
    fee = get_object_or_404(StudentFee, id=fee_id, school=school) 
    
    if request.method == 'POST':
        form = StudentFeeForm(request.POST, instance=fee)
        if form.is_valid():
            form.save()
            messages.success(request, f"Fees updated for {fee.student.get_full_name()}")
            return redirect(f"{reverse('accounts:student_fees_list')}?term={fee.term.id}")  
        else:
            messages.error(request, "Please fix the errors below")
    else:
        form = StudentFeeForm(instance=fee)
    
    return render(request, 'accounts/edit_student_fee.html', {
        'form': form,
        'fee': fee
    })

@login_required
def receipt_pdf(request, payment_id):
    school = request.user.school
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related('student', 'recorded_by'),
        id=payment_id,
        student__school=school  
    )

    display_text = payment.get_payment_types_display()
    if display_text == "—":
        fee_items = [('School Fees', payment.total_amount)]
    else:
        names = display_text.split('\n')
        amount_per_item = payment.total_amount / len(names)
        fee_items = [(name, amount_per_item) for name in names]

    context = {
        'payment': payment,
        'school': school, 
        'accountant': payment.recorded_by,
        'total_paid': payment.total_amount,
        'fee_items': fee_items,
    }

    html_string = render_to_string('accounts/receipt_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename=receipt-{payment.receipt_number}.pdf'

    pisa_status = pisa.CreatePDF(io.StringIO(html_string), dest=response)
    if pisa_status.err:
        return HttpResponse(f"PDF error: {pisa_status.err}", status=500)
    return response


class CustomLoginView(LoginView):
    def get_success_url(self):
        user = self.request.user
        if user.is_superuser or user.is_staff:
            return '/accountant/'
        return '/student/dashboard/'


from django.views.decorators.http import require_GET

@login_required
@require_GET  
def mark_announcements_read(request):
    ann_id = request.GET.get('id')
    if ann_id:
        announcement = get_object_or_404(Announcement, pk=ann_id)
        announcement.acknowledged_by.add(request.user)
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'No ID provided'})

@login_required
def acknowledge_announcement(request, pk):
    """For HIGH priority: Mark as acknowledged when user clicks 'I Understand'"""
    announcement = get_object_or_404(Announcement, pk=pk)
    
    # Mark as acknowledged
    announcement.acknowledged_by.add(request.user)
    
    # Also mark as read
    AnnouncementRead.objects.get_or_create(
        announcement=announcement,
        user=request.user
    )
    
    # If there's a button link, go there
    if announcement.action_url:
        return redirect(announcement.action_url)
    
    # Otherwise go back to dashboard
    return redirect('accounts:student_dashboard')


def is_admin(user):
    return user.role == 'admin'

from .sms_service import send_sms_to_school, format_ghana_number

@login_required
def create_announcement(request):
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        send_sms = request.POST.get('send_sms') == 'on'  # checkbox
        
        if form.is_valid():
            ann = form.save(commit=False)
            ann.school = request.user.school
            ann.created_by = request.user
            ann.target_roles = form.cleaned_data['target_roles']
            ann.save()

            target_roles = [r.lower() for r in (ann.target_roles or [])]
            
            users = User.objects.none()
            group_name = ""

            if 'all' in target_roles:
                users = User.objects.filter(school=request.user.school)
                group_name = "All Users"
            else:
                q = Q()
                for r in target_roles:
                    q |= Q(role__icontains=r)
                users = User.objects.filter(school=request.user.school).filter(q)
                group_name = ", ".join(target_roles).title()

                if 'student' in target_roles:
                    student_qs = users.filter(role__icontains='student')
                    if ann.target_class:
                        try:
                            student_qs = student_qs.filter(school_class=ann.target_class)
                        except:
                            pass
                    for stu in student_qs:
                        try:
                            if hasattr(stu, 'parent') and stu.parent:
                                users = users | User.objects.filter(id=stu.parent.id)
                        except:
                            pass

            if ann.target_class:
                class_name = ann.target_class.name
                group_name = f"{class_name}" if 'all' in target_roles else f"{group_name} - {class_name}"
                if 'student' in target_roles:
                    non_students = users.exclude(role__icontains='student')
                    students_in_class = User.objects.filter(school=request.user.school, role__icontains='student', school_class=ann.target_class)
                    users = non_students | students_in_class

            notif = Notification.objects.create(
                title=ann.title,
                message=ann.message,
                created_by=request.user,
                target_group=group_name
            )
            users = users.distinct().exclude(id=request.user.id)
            
            NotificationRecipient.objects.bulk_create([
                NotificationRecipient(notification=notif, user=u) for u in users
            ], ignore_conflicts=True)
            
            NotificationRecipient.objects.get_or_create(notification=notif, user=request.user)

            # ===== SMS PART - FREE TEST / LIVE =====
            sms_result = ""
            if send_sms and request.user.school.sms_enabled:
                # Collect phone numbers from target users
                phone_numbers = []
                for u in users:
                    # Try all possible phone fields
                    if u.phone:
                        phone_numbers.append(u.phone)
                    if hasattr(u, 'parent_guardian_phone') and u.parent_guardian_phone:
                        phone_numbers.append(u.parent_guardian_phone)
                    if hasattr(u, 'parent') and u.parent and u.parent.phone:
                        phone_numbers.append(u.parent.phone)
                
                # Shorten message for SMS (160 chars best)
                sms_msg = f"{ann.title}: {ann.message[:100]}"
                
                success, result_msg = send_sms_to_school(
                    request.user.school,
                    phone_numbers,
                    sms_msg
                )
                if success:
                    sms_result = f" | SMS: {result_msg}"
                else:
                    sms_result = f" | SMS Failed: {result_msg}"

            messages.success(request, f"Sent to {users.count()} users: {group_name}{sms_result}")
            return HttpResponseRedirect('/notifications/')
    else:
        form = AnnouncementForm()
    return render(request, 'accounts/create_announcement.html', {'form': form, 'school': request.user.school})


def delete_announcement(request, pk):
    notif = Notification.objects.filter(pk=pk).first()
    if not notif:
        from .models import NotificationRecipient
        rec = NotificationRecipient.objects.filter(pk=pk).first()
        if rec:
            notif = rec.notification
    
    if notif:
        Announcement.objects.filter(title=notif.title).delete()
        notif.delete()
        return HttpResponseRedirect('/notifications/')

def edit_announcement(request, pk):
    notif = Notification.objects.filter(pk=pk).first()
    if not notif:
        return HttpResponseRedirect('/notifications/')
    
    ann = Announcement.objects.filter(title=notif.title).first()
    if request.method == 'POST':
        notif.title = request.POST.get('title')
        notif.message = request.POST.get('message')
        notif.save()
        if ann:
            ann.title = notif.title
            ann.message = notif.message
            ann.save()
        return HttpResponseRedirect('/notifications/')
    
    return render(request, 'accounts/edit_announcement.html', {'notif': notif, 'ann': ann})

def school_payment_settings(request):
    school = request.user.school  
    
    if request.method == 'POST':
        form = SchoolPaymentSettingsForm(request.POST, instance=school)
        if form.is_valid():
            form.save()
            messages.success(request, "Payment settings saved successfully!")
            return redirect('/settings/payment/')
    else:
        form = SchoolPaymentSettingsForm(instance=school)
    
    return render(request, 'accounts/school_payment_settings.html', {'form': form})

@login_required
def fee_list_page(request):
    fee_structures = FeeStructure.objects.filter(school=request.user.school, is_published=True)
    years = FeeStructure.objects.filter(school=request.user.school).values_list('academic_year', flat=True).distinct()
    terms = Term.objects.filter(school=request.user.school)
    fee_breakdown = FeeStructure.objects.filter(school=request.user.school, is_active=True, is_published=True).first()
    
    selected_year = request.GET.get('year')
    selected_term = request.GET.get('term')
    
    if selected_year:
        fee_structures = fee_structures.filter(academic_year=selected_year)
    if selected_term:
        fee_structures = fee_structures.filter(term_id=selected_term)
    
    return render(request, 'accounts/finance_fee_list.html', {
        'fee_structures': fee_structures,
        'years': years,
        'terms': terms,
        'selected_year': selected_year,
        'selected_term': selected_term,
        'fee_breakdown': fee_breakdown,
    })


@login_required
@csrf_exempt
def verify_paystack_payment(request):
    try:
        data = json.loads(request.body)
        reference = data.get('reference')
        fee_id = data.get('fee_id')
        items = data.get('items', [])
        
        if not items:
            return JsonResponse({'status': False, 'message': 'No fees selected'})
        if not fee_id:
            return JsonResponse({'status': False, 'message': 'Fee ID missing'})
        
        school = School.objects.first()
        if not school or not school.paystack_secret_key:
            return JsonResponse({'status': False, 'message': 'School secret key missing'})
        
        r = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {school.paystack_secret_key}'}
        )
        res = r.json()
        
        if not res.get('status') or res['data']['status'] != 'success':
            return JsonResponse({'status': False, 'message': 'Payment verification failed'})
        
        amount_ghs = Decimal(res['data']['amount']) / Decimal(100)
        fee = StudentFee.objects.get(id=fee_id)
        
        items_total = sum(Decimal(str(item['amount'])) for item in items)
        if abs(items_total - amount_ghs) > Decimal('0.01'):
            return JsonResponse({'status': False, 'message': 'Amount mismatch'})
        
        ay_obj, _ = AcademicYear.objects.get_or_create(
            name=fee.academic_year,
            defaults={'is_active': False}
        )
        
        with transaction.atomic():  # Added atomic for safety
            txn = PaymentTransaction.objects.create(
                student = fee.student,
                total_amount = amount_ghs,
                amount_paid = amount_ghs,
                term = fee.term,
                academic_year = ay_obj,
                payment_method = 'paystack',
                recorded_by = request.user
            )
            
            for item in items:
                fee_name = item.get('fee_name')
                paid_field = item.get('paid_field')
                item_amount = Decimal(str(item['amount']))
                
                if not paid_field:
                    return JsonResponse({'status': False, 'message': f'Fee type not specified for {fee_name}'})
                if not hasattr(fee, paid_field):
                    return JsonResponse({'status': False, 'message': f'Invalid fee field: {paid_field}'})
                
                current_paid = getattr(fee, paid_field) or Decimal(0)
                setattr(fee, paid_field, current_paid + item_amount)
                
                PaymentItem.objects.create(
                    transaction = txn,
                    fee_name = fee_name,
                    amount = item_amount
                )
            
            fee.save()

            # ===== NEW - ADD THIS FOR ADMIN MONITORING =====
            FeeAuditLog.objects.create(
                transaction=txn,
                action='COLLECTED',
                done_by=request.user,
                details=f"PAYSTACK Auto - GHS {amount_ghs} - Ref {reference} - {fee.student.get_full_name()} - {', '.join([i['fee_name'] for i in items])}"
            )
            # ===== END NEW =====
        
        return JsonResponse({
            'status': True,
            'receipt': txn.receipt_number,
            'balance': float(fee.balance())
        })
        
    except StudentFee.DoesNotExist:
        return JsonResponse({'status': False, 'message': 'Fee record not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': False, 'message': str(e)}, status=500)

@login_required
def fee_history(request, fee_id):
    fee = get_object_or_404(StudentFee, id=fee_id)
    
    # Get the AcademicYear object from the string in StudentFee
    try:
        academic_year_obj = AcademicYear.objects.get(name=fee.academic_year)
    except AcademicYear.DoesNotExist:
        academic_year_obj = None
    
    # Filter PaymentTransaction using the AcademicYear object, not the string
    transactions = PaymentTransaction.objects.filter(
        student=fee.student,
        term=fee.term,
        academic_year=academic_year_obj  # Pass the object, not '2025/2026'
    ).prefetch_related('items').order_by('-created_at') if academic_year_obj else PaymentTransaction.objects.none()
    
    return render(request, 'accounts/fee_history.html', {
        'fee': fee,
        'transactions': transactions,
        'student': fee.student,
    })


@login_required
def student_payment_history(request):
    if request.user.role != 'student':
        return redirect('accounts:dashboard')
    
    student = request.user

    # MULTI-SCHOOL FIX
    years = AcademicYear.objects.filter(school=student.school).order_by('-name')
    terms = Term.objects.filter(school=student.school).order_by('-start_date')

    year_id = request.GET.get('year')
    term_id = request.GET.get('term')

    payments_qs = PaymentTransaction.objects.filter(
        student=student
    ).select_related('student', 'term', 'academic_year')

    pending_base_qs = PendingPayment.objects.filter(student=student).select_related('student', 'term', 'academic_year')

    if year_id:
        payments_qs = payments_qs.filter(academic_year_id=year_id)
        pending_base_qs = pending_base_qs.filter(academic_year_id=year_id)
    
    if term_id:
        payments_qs = payments_qs.filter(term_id=term_id)
        pending_base_qs = pending_base_qs.filter(term_id=term_id)

    payments = payments_qs.order_by('-created_at')
    total_amount = payments_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0

    all_pending_records = pending_base_qs.order_by('-submitted_at')
    pending_payments = all_pending_records.filter(status='pending')
    rejected_payments = all_pending_records.filter(status='rejected')

    pending_count = pending_payments.count()
    pending_amount = pending_payments.aggregate(total=Sum('amount'))['total'] or 0

    return render(request, 'accounts/student_payment_history.html', {
        'payments': payments,
        'pending_payments': pending_payments,
        'rejected_payments': rejected_payments,
        'pending_count': pending_count,
        'pending_amount': pending_amount,
        'total_amount': total_amount,
        'student': student,
        'school': student.school,
        'years': years,
        'terms': terms,
        'selected_year': year_id or "",
        'selected_term': term_id or "",
    })


@login_required
def link_students_to_parent(request, parent_id):
    if request.user.role != 'admin':
        return redirect('accounts:dashboard')

    parent = get_object_or_404(User, id=parent_id, role='parent')
    
    classes = SchoolClass.objects.filter(school=request.user.school).order_by('name')
    selected_class_id = request.GET.get('class_id')
    search_q = request.GET.get('q', '')

    students = User.objects.filter(role='student', school=request.user.school, is_active=True)

    if selected_class_id:
        students = students.filter(school_class_id=selected_class_id)
    
    if search_q:
        students = students.filter(
            models.Q(first_name__icontains=search_q) | 
            models.Q(last_name__icontains=search_q) |
            models.Q(student_number__icontains=search_q)
        )

    students = students.order_by('last_name')

    # === PAGINATION - 10 per page ===
    paginator = Paginator(students, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    # ================================

    if request.method == "POST":
        selected_class_id_post = request.POST.get('selected_class_id')
        selected_students = request.POST.getlist('students')
        
        if selected_class_id_post:
            class_student_ids = User.objects.filter(
                school_class_id=selected_class_id_post,
                school=request.user.school,
                role='student'
            ).values_list('id', flat=True)
            ParentStudentLink.objects.filter(parent=parent, student_id__in=class_student_ids).delete()
        else:
            ParentStudentLink.objects.filter(parent=parent).delete()

        for student_id in selected_students:
            rel = request.POST.get(f'relationship_{student_id}') or None
            student = User.objects.get(id=student_id, role='student')
            ParentStudentLink.objects.create(parent=parent, student=student, relationship=rel)

        messages.success(request, "Students linked successfully!")
        if selected_class_id_post:
            return redirect(f"{request.path}?class_id={selected_class_id_post}")
        return redirect('accounts:manage-parents')

    linked_map = dict(ParentStudentLink.objects.filter(parent=parent).values_list('student_id', 'relationship'))
    for s in page_obj:
        s.link_relationship = linked_map.get(s.id, '')

    return render(request, 'accounts/link_students_to_parent.html', {
        'parent': parent,
        'students': page_obj,  # <-- send page_obj
        'classes': classes,
        'selected_class_id': selected_class_id,
        'search_q': search_q,
        'total_linked': len(linked_map),
    })


@login_required
def my_children(request):
    if request.user.role != 'parent':
        return redirect('accounts:parent_dashboard')

    children = ParentStudentLink.objects.filter(
        parent=request.user
    ).select_related('student', 'student__school_class')

    return render(request, 'accounts/my_children.html', {
        'children': children
    })


@login_required
def parent_payment_history(request):
    if request.user.role!= 'parent':
        return redirect('accounts:dashboard')
    school = request.user.school

    # Get all children linked to this parent
    links = ParentStudentLink.objects.filter(parent=request.user).select_related('student')
    children = [link.student for link in links]

    selected_child = None
    if len(children) == 1:
        selected_child = children[0]
    else:
        child_id = request.GET.get('child') or request.session.get('selected_child_id')
        if child_id:
            for child in children:
                if str(child.id) == str(child_id):
                    selected_child = child
                    break

    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    # Get approved payments from PaymentTransaction
    if selected_child:
        payments = PaymentTransaction.objects.filter(
            school=school,
            student=selected_child

        ).select_related('student', 'term', 'academic_year').order_by('-created_at')
    else:
        children_ids = [c.id for c in children]
        payments = PaymentTransaction.objects.filter(
            school=school,
            student__id__in=children_ids
        ).select_related('student', 'term', 'academic_year').order_by('-created_at')

    

    # Get ONLY LATEST PendingPayment per student_fee to avoid duplicates
    from django.db.models import OuterRef, Subquery

    if selected_child:
        base_pending_qs = PendingPayment.objects.filter(student=selected_child)
    else:
        base_pending_qs = PendingPayment.objects.filter(student__in=children)

    all_pending_records = base_pending_qs.select_related(
        'student', 'term', 'academic_year'
    ).order_by('-submitted_at')

    # Split by status for your tabs/cards
    pending_payments = all_pending_records.filter(status='pending')
    rejected_payments = all_pending_records.filter(status='rejected')
    academic_year_id = request.GET.get("academic_year")
    term_id = request.GET.get("term")

    if academic_year_id:
        payments = payments.filter(academic_year_id=academic_year_id)
        pending_payments = pending_payments.filter(academic_year_id=academic_year_id)
        rejected_payments = rejected_payments.filter(academic_year_id=academic_year_id)

    if term_id:
        payments = payments.filter(term_id=term_id)
        pending_payments = pending_payments.filter(term_id=term_id)
        rejected_payments = rejected_payments.filter(term_id=term_id)

    # Calculate after filtering
    total_amount = payments.aggregate(
        Sum('total_amount')
    )['total_amount__sum'] or 0

    pending_count = pending_payments.count()
    pending_amount = pending_payments.aggregate(total=Sum('amount'))['total'] or 0
    academic_years = AcademicYear.objects.filter(
        school=school
    ).order_by("-name")

    terms = Term.objects.filter(
        school=school
    ).select_related("academic_year").order_by(
        "-academic_year__name",
        "term_number"
    )

    return render(request, 'accounts/parent_payment_history.html', {
        "academic_years": academic_years,
        "terms": terms,
        'payments': payments,
        'pending_payments': pending_payments,
        'rejected_payments': rejected_payments,
        'pending_count': pending_count,
        'pending_amount': pending_amount,
        'total_amount': total_amount,
        'children': children,
        'selected_child': selected_child,
        'show_switcher': len(children) > 1,
    })


@login_required
def student_terminal_report(request):
    if request.user.role != 'student':
        return redirect('accounts:dashboard')

    student = request.user
    school = student.school_class.school if student.school_class else student.school

    all_terms = Term.objects.filter(
        result__student=student,
        result__status='published'
    ).distinct().order_by('-start_date')

    term_id = request.GET.get('term_id')
    selected_term = all_terms.filter(id=term_id).first() if term_id else all_terms.first()

    term_data = None
    summary = None
    remark = None

    if selected_term:
        results = Result.objects.filter(
            student=student,
            term=selected_term,
            status='published'
        ).select_related('subject', 'term', 'term__academic_year').order_by('subject__name')

        for r in results:
            ca_val = float(ContinuousAssessment.objects.filter(
                student=student, subject=r.subject, term=selected_term
            ).aggregate(total=Sum('score'))['total'] or r.class_score or 0)
            ex_val = float(r.exam_score or 0)
            r.calc_total = ca_val + ex_val
            r.calc_ca = ca_val
            r.calc_exam = ex_val
            if r.calc_total >= 80: r.calc_grade, r.calc_remark = "A", "Excellent"
            elif r.calc_total >= 70: r.calc_grade, r.calc_remark = "B", "Very Good"
            elif r.calc_total >= 60: r.calc_grade, r.calc_remark = "C", "Good"
            elif r.calc_total >= 50: r.calc_grade, r.calc_remark = "D", "Pass"
            elif r.calc_total >= 40: r.calc_grade, r.calc_remark = "E", "Weak"
            else: r.calc_grade, r.calc_remark = "F", "Fail"

        remark = StudentRemark.objects.filter(student=student, term=selected_term).first()

        class_score = sum(float(r.calc_ca or 0) for r in results)
        exam_score = sum(float(r.calc_exam or 0) for r in results)
        total_score = class_score + exam_score
        n = len(results) or 1
        percentage = round(total_score / n, 1)

        term_name = getattr(selected_term, 'name', str(selected_term))
        academic_year_obj = getattr(selected_term, 'academic_year', None)
        summary_qs = StudentTermSummary.objects.filter(student=student, term=term_name)
        if academic_year_obj:
            summary_qs = summary_qs.filter(academic_year=academic_year_obj)
        summary = summary_qs.first()

        position = 1
        class_size = 1
        overall_grade = 'F'
        overall_remark = 'Fail'

        if summary:
            if summary.term_total: total_score = float(summary.term_total)
            if summary.term_average: percentage = float(summary.term_average)
            if summary.term_grade: overall_grade = summary.term_grade
            if summary.term_remark: overall_remark = summary.term_remark
            if summary.rank: position = summary.rank
        else:
            if percentage >= 80: overall_grade, overall_remark = 'A', 'Excellent'
            elif percentage >= 70: overall_grade, overall_remark = 'B', 'Very Good'
            elif percentage >= 60: overall_grade, overall_remark = 'C', 'Good'
            elif percentage >= 50: overall_grade, overall_remark = 'D', 'Pass'
            else: overall_grade, overall_remark = 'F', 'Fail'

        # === FIXED ATTENDANCE - FINAL RULE: ONLY P and A, 1 P = Whole day Present ===
        start = selected_term.start_date
        today = timezone.now().date()
        end = selected_term.end_date if selected_term.end_date and selected_term.end_date < today else today

        holiday_dates = set()
        events = AcademicCalendar.objects.filter(school=school, start_date__lte=end, end_date__gte=start, affects_timetable=True)
        for event in events:
            d = event.start_date
            while d <= event.end_date:
                if start <= d <= end:
                    holiday_dates.add(d)
                d += timedelta(days=1)

        attendance_records = AttendanceRecord.objects.filter(
            student=student, session__date__gte=start, session__date__lte=end
        ).exclude(session__date__in=holiday_dates).values('session__date', 'status')

        by_date = defaultdict(list)
        for rec in attendance_records:
            by_date[rec['session__date']].append(rec['status'])

        times_present = 0
        times_absent = 0
        for statuses in by_date.values():
            if 'P' in statuses:
                times_present += 1
            else:
                times_absent += 1

        times_late = 0

        days_opened = sum(1 for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5 and (start+timedelta(days=i)) not in holiday_dates)
        attendance_percentage = round((times_present / days_opened * 100), 1) if days_opened else 0

        term_data = {
            'term': selected_term,
            'results': results,
            'grand_class_score': class_score,
            'grand_exam_score': exam_score,
            'grand_total_score': round(total_score,1),
            'grand_percentage': percentage,
            'overall_grade': overall_grade,
            'overall_remark': overall_remark,
            'class_position': position,
            'class_size': class_size,
            'summary': summary,
            'days_opened': days_opened,
            'times_present': times_present,
            'times_absent': times_absent,
            'times_late': times_late,
            'attendance_percentage': attendance_percentage,
        }

    context = {
        'terms': all_terms,
        'selected_term': selected_term,
        'term_data': term_data,
        'summary': summary,
        'school': school,
        'remark': remark,
        'student': student,
        'selected_child': student,
    }
    return render(request, 'accounts/view_child_results.html', context)

@login_required
def submit_manual_payment(request, student_fee_id):
    if request.user.role not in ['parent', 'student']:
        messages.error(request, "Access denied.")
        return redirect('accounts:dashboard')
    
    student_fee = get_object_or_404(StudentFee, id=student_fee_id)
    school = student_fee.student.school
    
    # Security check: Different logic for parent vs student
    if request.user.role == 'parent':
        is_linked = ParentStudentLink.objects.filter(
            parent=request.user,
            student=student_fee.student
        ).exists()
        if not is_linked:
            messages.error(request, "You are not authorized to submit payment for this student.")
            return redirect('accounts:parent_dashboard')
    
    elif request.user.role == 'student':
        if student_fee.student != request.user:
            messages.error(request, "You can only submit payment for your own fees.")
            return redirect('accounts:student_payment_history') 
        # DELETE THE ELSE BLOCK - let student continue
    
    rejected_payment = None
    resubmit_id = request.GET.get('resubmit')
    if resubmit_id:
        # Remove submitted_by=request.user so parent can resubmit student's rejected payment
        rejected_payment = PendingPayment.objects.filter(
            id=resubmit_id,
            student_fee=student_fee,
            status='rejected'
            # submitted_by=request.user  <-- REMOVE THIS
        ).first()
    
    try:
        term = Term.objects.get(
            term_number=student_fee.term.term_number if hasattr(student_fee.term, 'term_number') else student_fee.term,
            academic_year__name=student_fee.academic_year
        )
        academic_year = term.academic_year
    except Term.DoesNotExist:
        messages.error(request, "Term not found for this fee record.")
        if request.user.role == 'student':
            return redirect('accounts:view-student-fees')  # Use correct URL name
        else:
            return redirect('accounts:parent_dashboard')
    
    # Initialize form
    if rejected_payment:
        form = PendingPaymentForm(request.POST or None, request.FILES or None, instance=rejected_payment, school=school)
        if request.method == 'GET':
            messages.info(request, f"Resubmitting rejected payment. Previous reason: {rejected_payment.rejection_reason}")
    else:
        form = PendingPaymentForm(request.POST or None, request.FILES or None, school=school)
    
    if request.method == 'POST':
        if form.is_valid():
            pending = form.save(commit=False)
            pending.student = student_fee.student
            pending.student_fee = student_fee
            pending.term = term
            pending.academic_year = academic_year
            pending.submitted_by = request.user
            pending.status = 'pending'
            pending.rejection_reason = None
            pending.save()

            if rejected_payment:
                pending.items.all().delete()

            items = json.loads(request.POST.get("selected_items", "[]"))
            for item in items:
                PendingPaymentItem.objects.create(
                    pending_payment=pending,
                    fee_name=item["fee_name"],
                    paid_field=item["paid_field"],
                    amount=Decimal(item["amount"])
                )

            if rejected_payment:
                messages.success(request, "Payment proof updated and resubmitted. Awaiting accountant verification.")
            else:
                messages.success(request, "Payment proof submitted. Awaiting accountant verification.")
            
            if request.user.role == 'student':
                return redirect("accounts:view-student-fees")  # Use correct URL name
            else:
                return redirect("accounts:parent_payment_history")
    
        if rejected_payment:
            form = PendingPaymentForm(request.POST or None, request.FILES or None, instance=rejected_payment, school=school)
            if request.method == 'GET' and not request.session.get(f'shown_resubmit_{resubmit_id}'):
                messages.info(request, f"Resubmitting rejected payment. Previous reason: {rejected_payment.rejection_reason}")
                request.session[f'shown_resubmit_{resubmit_id}'] = True
        else:
            form = PendingPaymentForm(request.POST or None, request.FILES or None, school=school)
    
    if request.method == 'POST':
        if form.is_valid():
            pending = form.save(commit=False)
            pending.student = student_fee.student
            pending.student_fee = student_fee
            pending.term = term
            pending.academic_year = academic_year
            pending.submitted_by = request.user
            pending.status = 'pending'
            pending.rejection_reason = None
            pending.save()

            if rejected_payment:
                pending.items.all().delete()

            items = json.loads(request.POST.get("selected_items", "[]"))
            for item in items:
                PendingPaymentItem.objects.create(
                    pending_payment=pending,
                    fee_name=item["fee_name"],
                    paid_field=item["paid_field"],
                    amount=Decimal(item["amount"])
                )

            if rejected_payment:
                messages.success(request, "Payment proof updated and resubmitted. Awaiting accountant verification.")
            else:
                messages.success(request, "Payment proof submitted. Awaiting accountant verification.")
            
            if request.user.role == 'student':
                return redirect("accounts:student_view_fees")
            else:
                return redirect("accounts:parent_payment_history")
        # If form is NOT valid, it falls through to render below with errors
    
    # FIXED: Calculate real balance from PaymentTransaction
    real_total_paid = PaymentTransaction.objects.filter(
        student=student_fee.student,
        student__school=school
    ).aggregate(total=Sum('total_amount'))['total'] or 0

    real_balance = float(student_fee.total_amount) - float(real_total_paid)
    if real_balance < 0:
        real_balance = 0

    # Build payment_options
    payment_options = []
    fee_fields = [
        ("School Fees", "school_fees", "amount_paid_school_fees"),
        ("Feeding Fee", "canteen_amount", "amount_paid_canteen"),
        ("Boarding Fee", "boarding_fee", "amount_paid_boarding_fee"),
        ("Hostel Fee", "hostel_fee", "amount_paid_hostel_fee"),
        ("Development Fee", "development_fee", "amount_paid_development_fee"),
        ("PTA Dues", "pta_dues", "amount_paid_pta_dues"),
        ("Computer Levy", "computer_levy", "amount_paid_computer_levy"),
        ("Exam Fees", "exam_fees", "amount_paid_exam_fees"),
        ("Other Fees", "other_fees", "amount_paid_other_fees"),
    ]

    paid_by_type = PaymentItem.objects.filter(
        transaction__student=student_fee.student,
        transaction__term=student_fee.term,
        transaction__academic_year__name=student_fee.academic_year
    ).values('fee_name').annotate(total=Sum('amount'))

    paid_map = {p['fee_name']: float(p['total']) for p in paid_by_type}

    for label, fee_field, paid_field in fee_fields:
        total = getattr(student_fee, fee_field, 0) or 0
        paid = paid_map.get(label, 0)
        balance = float(total) - paid
        if balance > 0:
            payment_options.append({
                "label": label,
                "paid_field": paid_field,
                "balance": balance,
            })
    
    context = {
        'form': form,  # This will now contain errors if validation failed
        'student_fee': student_fee,
        'student': student_fee.student,
        'balance': real_balance,  # Fixed
        'school': school,
        'payment_options': payment_options,
        'is_resubmitting': rejected_payment is not None,
    }
    return render(request, 'accounts/submit_manual_payment.html', context)  # THIS ALWAYS RUNS NOW


@login_required
def pending_payments(request):
    if request.user.role != 'accountant':
        messages.error(request, "You are not authorized to view this page.")
        return redirect('accounts:dashboard')

    school = request.user.school

    pending_payments = PendingPayment.objects.filter(
        student__school=school,
        status='pending'
    ).select_related(
        'student',
        'student_fee',
        'term',
        'academic_year',
        'submitted_by'
    ).order_by('-submitted_at')


    context = {
        'pending_payments': pending_payments,
    }

    return render(
        request,
        'accounts/pending_payments.html',
        context
    )


@login_required
def pending_payment_detail(request, payment_id):

    if request.user.role != 'accountant':
        messages.error(request, "You are not authorized to view this page.")
        return redirect('accounts:dashboard')

    payment = get_object_or_404(
        PendingPayment.objects.prefetch_related('items'),
        id=payment_id,
        student__school=request.user.school
    )

    context = {
        'payment': payment,
        'payment_items': payment.items.all(),
    }

    return render(
        request,
        'accounts/pending_payment_detail.html',
        context
    )


@login_required
def approve_manual_payment(request, payment_id):

    if request.user.role != "accountant":
        messages.error(request, "You are not authorized to perform this action.")
        return redirect("accounts:dashboard")

    payment = get_object_or_404(
        PendingPayment.objects.prefetch_related("items"),
        id=payment_id,
        student__school=request.user.school,
        status="pending"
    )

    if request.method != "POST":
        return redirect("accounts:pending_payment_detail", payment_id=payment.id)

    try:
        with transaction.atomic():
            fee = payment.student_fee

            txn = PaymentTransaction.objects.create(
                student=payment.student,
                total_amount=payment.amount,
                amount_paid=payment.amount,
                term=payment.term,
                academic_year=payment.academic_year,
                payment_method=payment.payment_method,
                recorded_by=request.user,
                transaction_id=payment.reference_number,
            )

            for item in payment.items.all():
                PaymentItem.objects.create(
                    transaction=txn,
                    fee_name=item.fee_name,
                    amount=item.amount,
                )
                current_paid = getattr(fee, item.paid_field, Decimal("0"))
                setattr(fee, item.paid_field, current_paid + item.amount)
                code = item.fee_name.upper().replace(" ", "_")
                payment_type, created = PaymentType.objects.get_or_create(
                    code=code, defaults={"name": item.fee_name}
                )
                txn.payment_types.add(payment_type)
            fee.save()

            payment.status = "approved"
            payment.verified_by = request.user
            payment.verified_at = timezone.now()
            payment.payment_transaction = txn
            payment.save()

            # === NEW: ADMIN MONITORING - Log online payment approval ===
            FeeAuditLog.objects.create(
                transaction=txn,
                action='COLLECTED',
                done_by=request.user,
                details=f"ONLINE Approved - {payment.payment_method} {payment.reference_number} - GHS {payment.amount} for {payment.student.get_full_name()}"
            )
            # === END NEW ===

            messages.success(request, f"Payment approved successfully. Receipt: {txn.receipt_number}")
            return redirect("accounts:pending_payments")

    except Exception as e:
        messages.error(request, f"Error approving payment: {e}")

    return redirect("accounts:pending_payment_detail", payment_id=payment.id)


@login_required
def reject_manual_payment(request, payment_id):

    if request.user.role != "accountant":
        messages.error(request, "You are not authorized to perform this action.")
        return redirect("accounts:dashboard")

    payment = get_object_or_404(
        PendingPayment,
        id=payment_id,
        student__school=request.user.school,
        status="pending"
    )

    if request.method == "POST":

        reason = request.POST.get("rejection_reason")

        payment.status = "rejected"
        payment.rejection_reason = reason
        payment.verified_by = request.user
        payment.verified_at = timezone.now()
        payment.save()

        messages.success(
            request,
            "Payment has been rejected."
        )

        return redirect("accounts:pending_payments")

    return redirect(
        "accounts:pending_payment_detail",
        payment_id=payment.id
    )


@login_required
def parent_timetable(request):
    if request.user.role!= 'parent':
        return redirect('accounts:dashboard')

    # SAME CHILD SWITCHER LOGIC AS YOUR parent_payment_history
    links = ParentStudentLink.objects.filter(parent=request.user).select_related('student__school_class')
    children = [link.student for link in links]

    selected_child = None
    if len(children) == 1:
        selected_child = children[0]
    else:
        child_id = request.GET.get('child') or request.session.get('selected_child_id')
        if child_id:
            for child in children:
                if str(child.id) == str(child_id):
                    selected_child = child
                    break

    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    # FALLBACK TO FIRST CHILD ONLY IF NO CHILD IN URL/SESSION
    if not selected_child and children:
        selected_child = children[0]

    # CRITICAL FIX: Reset everything if child has no class
    timetable_by_day = []
    today_date = timezone.now().date()
    today_weekday = timezone.now().weekday()
    week_offset = 0
    start_of_week = None

    if selected_child and selected_child.school_class: # CHECK CLASS EXISTS
        student_class = selected_child.school_class

        try:
            week_offset = int(request.GET.get('week', '0') or 0)
        except ValueError:
            week_offset = 0

        start_of_week = today_date - timedelta(days=today_date.weekday())
        start_of_week += timedelta(weeks=week_offset)
        current_week_number = start_of_week.isocalendar()[1]

# ONLY QUERY TIMETABLE FOR THIS CHILD'S CLASS
        timetable = Timetable.objects.filter(
            school_class=student_class
        ).select_related('subject', 'teacher').order_by('weekday', 'period')

        # FIX: Check if any timetable entries exist BEFORE building days
        has_timetable = timetable.exists() # ← ADD THIS LINE

        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

        # Only build timetable_by_day if there are actual entries
        if has_timetable: # ← WRAP THE LOOP IN THIS
            for day_num in range(5):
                current_date = start_of_week + timedelta(days=day_num)

                event = AcademicCalendar.objects.filter(
                    start_date__lte=current_date,
                    end_date__gte=current_date
                ).first()

                entries = [e for e in timetable if e.weekday == day_num]

                if event and event.affects_timetable:
                    entries = []

                timetable_by_day.append({
                    'num': day_num,
                    'name': days[day_num],
                    'date': current_date,
                    'entries': entries,
                    'event': event,
                })

    context = {
        'children': children,
        'selected_child': selected_child,
        'show_switcher': len(children) > 1,
        'timetable_by_day': timetable_by_day,
        'has_timetable': has_timetable, 
        'today': today_date,
        'today_weekday': today_weekday if week_offset == 0 else -1,
        'week_offset': week_offset,
        'prev_week': week_offset - 1,
        'next_week': week_offset + 1,
        'current_week_start': start_of_week,
        'current_week_number': current_week_number,
        'has_timetable': bool(timetable_by_day), # NEW: For template check
    }
    return render(request, 'accounts/parent_timetable.html', context)


@login_required
def parent_attendance(request):
    if request.user.role!= 'parent':
        messages.error(request, "Only parents can view this page.")
        return redirect('accounts:dashboard')

    links = ParentStudentLink.objects.filter(parent=request.user).select_related('student')
    children = [link.student for link in links]

    selected_child = None
    if len(children) == 1:
        selected_child = children[0]
    else:
        child_id = request.GET.get('child') or request.session.get('selected_child_id')
        if child_id:
            for child in children:
                if str(child.id) == str(child_id):
                    selected_child = child
                    break
    if selected_child:
        request.session['selected_child_id'] = selected_child.id
    if not selected_child and children:
        selected_child = children[0]

    if not selected_child:
        return render(request, 'accounts/parent_attendance.html', {'children': [], 'has_records': False})
    school = selected_child.school

    setting = SchoolSetting.objects.filter(school=school).first()
    attendance_mode = setting.attendance_mode if setting else 'subject'
    term_id = request.GET.get('term')

    if term_id:
        active_term = Term.objects.filter(
            id=term_id,
            school=school
        ).first()

        if not active_term:
            active_term = Term.objects.filter(
                school=school,
                is_active=True
            ).first()
    else:
        active_term = Term.objects.filter(
            school=school,
            is_active=True
        ).first()

        if not active_term:
            active_term = Term.objects.filter(
                school=school
            ).order_by('-end_date').first()
    today = active_term.start_date if active_term else timezone.now().date()


    term_id = request.GET.get("term")

    try:
        week_offset = int(request.GET.get("att_week", 0))
    except (TypeError, ValueError):
        week_offset = 0
    if active_term and active_term.start_date:

        term_start = active_term.start_date
            # If no attendance week is supplied, open the current week
    if "att_week" not in request.GET:
        if active_term.start_date <= timezone.now().date():
            days_since_start = (timezone.now().date() - active_term.start_date).days
            if days_since_start < 0:
                week_offset = 0
            else:
                week_offset = days_since_start // 7

        # Week starts exactly from admin's selected opening date
# Week 1 = opening day to that week's Friday
        if week_offset == 0:
            target_monday = term_start

            days_to_friday = 4 - term_start.weekday()
            if days_to_friday < 0:
                days_to_friday = 0

            target_friday = term_start + timedelta(days=days_to_friday)

        # Week 2 onwards = Monday to Friday
        else:
            first_monday = term_start + timedelta(days=(7 - term_start.weekday()))

            target_monday = first_monday + timedelta(weeks=week_offset - 1)
            target_friday = target_monday + timedelta(days=4)

    else:

        current_monday = today - timedelta(days=today.weekday())
        target_monday = current_monday + timedelta(weeks=week_offset)
        target_friday = target_monday + timedelta(days=4)
    if active_term and target_friday > active_term.end_date:
        target_friday = active_term.end_date

    if active_term and active_term.start_date:
        term_start = active_term.start_date

        # Week 1 starts exactly from admin's start date
        days_from_start = (target_monday - term_start).days

        if days_from_start < 0:
            att_active_week = 1
        else:
            att_active_week = ((days_from_start + 1) // 7) + 1

    else:
        att_active_week = 1

    att_prev = week_offset - 1 if week_offset > 0 else None

    # ==========================
    # CLASS TEACHER MODE - ONLY P and A
    # ==========================
    if attendance_mode == 'class_teacher':
        raw = AttendanceRecord.objects.filter(
                student=selected_child,
                session__school_class__school=school,
                session__date__gte=target_monday,
                session__date__lte=target_friday
            ).values('session__date', 'status')

        by_date = defaultdict(list)
        for r in raw:
            by_date[r['session__date']].append(r['status'])

        records_list = []
        for date, statuses in by_date.items():
            # FINAL RULE: If any P that day = P, else A
            final_status = 'P' if 'P' in statuses else 'A'
            records_list.append({'session__date': date, 'status': final_status})

        records = sorted(records_list, key=lambda x: x['session__date'], reverse=True)
        total_count = len(records)
        present_count = len([r for r in records if r['status'] == 'P'])
        absent_count = len([r for r in records if r['status'] == 'A'])
        late_count = 0
        by_subject = []
        has_records = total_count > 0

    # ==========================
    # SUBJECT MODE - FINAL RULE YOU WANT
    # ==========================
    else:
        records_qs = AttendanceRecord.objects.filter(
                student=selected_child,
                session__school_class__school=school,
                session__date__gte=target_monday,
                session__date__lte=target_friday
            ).select_related(
            'session__subject',
            'session__teacher'
        ).order_by('-session__date')

        # 1. Per Subject Breakdown - Truth
        subject_data = defaultdict(lambda: {'name': '', 'present': 0, 'absent': 0, 'total': 0})
        for r in records_qs:
            if r.session and r.session.subject:
                sub_name = r.session.subject.name
                subject_data[sub_name]['name'] = sub_name
                subject_data[sub_name]['total'] += 1
                if r.status == 'P':
                    subject_data[sub_name]['present'] += 1
                else:
                    subject_data[sub_name]['absent'] += 1

        by_subject = []
        for data in subject_data.values():
            data['percentage'] = round((data['present'] / data['total']) * 100) if data['total'] > 0 else 0
            by_subject.append(data)

        # 2. Overall Rate - If present for 1 class that day, whole day = Present
        by_date_overall = defaultdict(list)
        for r in records_qs:
            by_date_overall[r.session.date].append(r.status)

        total_count = len(by_date_overall) # Total DAYS
        present_count = 0
        absent_count = 0
        for statuses in by_date_overall.values():
            if 'P' in statuses:
                present_count += 1
            else:
                absent_count += 1

        late_count = 0
        has_records = records_qs.exists()
        records = records_qs
    if active_term:
        total_term_days = (active_term.end_date - active_term.start_date).days + 1
        last_attendance_week = ((total_term_days - 1) // 7) + 1
    else:
        last_attendance_week = 1
    academic_years = AcademicYear.objects.filter(
        school=school
    ).order_by("-name")

    terms = Term.objects.filter(
        school=school
    ).select_related(
        "academic_year"
    ).order_by(
        "-academic_year__name",
        "term_number"
    )

    context = {
        'children': children,
        'selected_child': selected_child,
        'academic_years': academic_years,
        'terms': terms,
        'show_switcher': len(children) > 1,
        'records': records,
        'total': total_count,
        'present': present_count,
        'absent': absent_count,
        'late': late_count,
        'by_subject': by_subject,
        'att_week_start': target_monday,
        'att_week_end': target_friday,
        'att_active_week': att_active_week,
        'last_attendance_week': last_attendance_week,
        'att_prev_week': att_prev if att_prev is not None else 0,
        'att_next_week': week_offset + 1 if active_term else 0,
        'att_week_offset': week_offset,
        'selected_term_id': term_id,
        'attendance_mode': attendance_mode,
        'active_term': active_term,
        'has_records': has_records,
    }
    return render(request, 'accounts/parent_attendance.html', context)

@login_required
def notifications_list(request):
    filter_type = request.GET.get('filter', 'all')
    
    notifs = NotificationRecipient.objects.filter(
        user=request.user
    ).select_related('notification', 'notification__created_by').order_by('-notification__created_at')

    now = timezone.now()
    today = now.date()
    
    if filter_type == 'today':
        notifs = notifs.filter(notification__created_at__date=today)
    elif filter_type == 'week':
        start_of_week = today - timedelta(days=today.weekday())
        notifs = notifs.filter(notification__created_at__date__gte=start_of_week)

    # --- PAGINATION: 10 per page ---
    paginator = Paginator(notifs, 4)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Group only the current page
    grouped = defaultdict(list)
    for n in page_obj:
        created = n.notification.created_at
        date_key = created.strftime('%B %d, %Y') if created else "Recent"
        grouped[date_key].append(n)

    NotificationRecipient.objects.filter(user=request.user, is_read=False).update(is_read=True)

    return render(request, 'accounts/notifications.html', {
        'grouped_notifications': dict(grouped),
        'page_obj': page_obj,
        'filter': filter_type,
        'total': notifs.count()
    })

@login_required
def get_notification_count(request):
    from .models import NotificationRecipient
    count = NotificationRecipient.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({'count': count})


def _filter_announcements_by_date(queryset, filter_type):
    today = timezone.now().date()
    if filter_type == 'today':
        return queryset.filter(created_at__date=today)
    elif filter_type == 'week':
        start_of_week = today - timedelta(days=today.weekday()) # Monday
        return queryset.filter(created_at__date__gte=start_of_week)
    return queryset # all

@login_required
def parent_notifications(request):
    filter_type = request.GET.get('filter', 'all')
    all_anns = Announcement.objects.all().order_by('-created_at')
    
    # 1. Date filter first (fast)
    all_anns = _filter_announcements_by_date(all_anns, filter_type)

    # 2. Role filter
    filtered = []
    for ann in all_anns:
        roles = [str(r).lower().strip() for r in (ann.target_roles or [])]
        if 'parent' in roles or 'parents' in roles or 'all' in roles:
            filtered.append(ann)

    # 3. Pagination - 4 per page
    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/parent_notifications.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'filter': filter_type,
        'total': len(filtered)
    })

@login_required
def teacher_notifications(request):
    filter_type = request.GET.get('filter', 'all')
    all_anns = _filter_announcements_by_date(
        Announcement.objects.all().order_by('-created_at'), filter_type
    )
    filtered = [ann for ann in all_anns if any(r in [str(x).lower().strip() for x in (ann.target_roles or [])] for r in ['teacher','teachers','all'])]

    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/teacher_notifications.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'filter': filter_type,
        'total': len(filtered)
    })

@login_required
def student_notifications(request):
    filter_type = request.GET.get('filter', 'all')
    all_anns = _filter_announcements_by_date(
        Announcement.objects.all().order_by('-created_at'), filter_type
    )
    filtered = [ann for ann in all_anns if any(r in [str(x).lower().strip() for x in (ann.target_roles or [])] for r in ['student','students','all'])]

    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/student_notifications.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'filter': filter_type,
        'total': len(filtered)
    })


@login_required
def accountant_notifications(request):
    filter_type = request.GET.get('filter', 'all')
    
    # Date filter
    all_anns = Announcement.objects.all().order_by('-created_at')
    today = timezone.now().date()
    if filter_type == 'today':
        all_anns = all_anns.filter(created_at__date=today)
    elif filter_type == 'week':
        start_of_week = today - timedelta(days=today.weekday())
        all_anns = all_anns.filter(created_at__date__gte=start_of_week)

    # Role filter for accountant
    filtered = []
    for ann in all_anns:
        roles = [str(r).lower().strip() for r in (ann.target_roles or [])]
        if 'accountant' in roles or 'accountants' in roles or 'all' in roles:
            filtered.append(ann)

    # Pagination - 4 per page
    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/accountant_notifications.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'filter': filter_type,
        'total': len(filtered)
    })


@login_required
def school_sms_settings(request):
    school = request.user.school
    if not school:
        school = School.objects.first()
    
    if request.method == 'POST':
        form = SchoolSmsSettingsForm(request.POST, instance=school)
        if form.is_valid():
            form.save()
            messages.success(request, "SMS Settings saved! Now go test announcement.")
            return redirect('accounts:school_sms_settings')
    else:
        form = SchoolSmsSettingsForm(instance=school)
    
    return render(request, 'accounts/school_sms_settings.html', {'form': form, 'school': school})


@login_required
def admin_fee_monitoring(request):
    if request.user.role != 'admin':
        return redirect('accounts:home')
    
    school = request.user.school
    today = date.today()
    period = request.GET.get('period', 'today')

    # Base queries
    base_tx = PaymentTransaction.objects.filter(
        student__school=school,
        is_voided=False
    )
    voided_qs = PaymentTransaction.objects.filter(
        student__school=school,
        is_voided=True
    )
    expense_qs = Expense.objects.filter(school=school)
    log_qs = FeeAuditLog.objects.filter(done_by__school=school)
    pending_qs = PendingPayment.objects.filter(student__school=school, status='pending')

    if period == 'today':
        base_tx = base_tx.filter(created_at__date=today)
        voided_qs = voided_qs.filter(voided_at__date=today)
        expense_qs = expense_qs.filter(expense_date=today)
        log_qs = log_qs.filter(created_at__date=today)
        title = f"Today - {today}"
    elif period == 'week':
        start_date = today - timedelta(days=7)
        base_tx = base_tx.filter(created_at__date__gte=start_date)
        voided_qs = voided_qs.filter(voided_at__date__gte=start_date)
        expense_qs = expense_qs.filter(expense_date__gte=start_date)
        log_qs = log_qs.filter(created_at__date__gte=start_date)
        title = "Last 7 Days"
    elif period == 'month':
        base_tx = base_tx.filter(created_at__month=today.month, created_at__year=today.year)
        voided_qs = voided_qs.filter(voided_at__month=today.month, voided_at__year=today.year)
        expense_qs = expense_qs.filter(expense_date__month=today.month, expense_date__year=today.year)
        log_qs = log_qs.filter(created_at_month=today.month, created_at_year=today.year)  
        title = f"This Month - {today.strftime('%B %Y')}"
    else:  # all
        period = 'all'
        title = "All Time - Everything"

    today_tx = base_tx
    today_total = today_tx.aggregate(total=Sum('total_amount'))['total'] or 0
    today_count = today_tx.count()

    cash_total = today_tx.filter(payment_method__iexact='cash').aggregate(total=Sum('total_amount'))['total'] or 0
    bank_total = today_tx.exclude(payment_method__iexact='cash').aggregate(total=Sum('total_amount'))['total'] or 0

    by_accountant = today_tx.filter(payment_method__iexact='cash').values(
        'recorded_by__username', 'recorded_by__first_name', 'recorded_by__last_name'
    ).annotate(total=Sum('total_amount'), count=Count('id')).order_by('-total')

    by_online = today_tx.exclude(payment_method__iexact='cash').select_related('student').order_by('-created_at')[:20]
    
    by_method = today_tx.values('payment_method').annotate(
        total=Sum('total_amount'), count=Count('id')
    )

    voided_today = voided_qs.select_related('student', 'voided_by').order_by('-voided_at')[:20]
    expenses_today = expense_qs.order_by('-expense_date')[:20]
    expense_total = expense_qs.aggregate(total=Sum('amount'))['total'] or 0

    cash_in_hand = float(cash_total) - float(expense_total)
    net_balance = float(today_total) - float(expense_total)  # NEW

    recent_logs = log_qs.select_related('transaction__student', 'done_by').order_by('-created_at')[:30]
    pending_count = pending_qs.count()

    context = {
        'today_total': today_total,
        'today_count': today_count,
        'cash_total': cash_total,
        'bank_total': bank_total,
        'by_accountant': by_accountant,
        'by_method': by_method,
        'voided_today': voided_today,
        'expenses_today': expenses_today,
        'expense_total': expense_total,
        'cash_in_hand': cash_in_hand,
        'net_balance': net_balance,  # NEW
        'recent_logs': recent_logs,
        'pending_count': pending_count,
        'today': title,
        'period': period,
        'by_online': by_online,
    }
    return render(request, 'accounts/admin_fee_monitoring.html', context)

@login_required
def void_transaction(request, txn_id):
    if request.user.role != 'admin':
        return redirect('accounts:home')

    txn = get_object_or_404(PaymentTransaction, id=txn_id, student__school=request.user.school)

    if request.method == 'POST':
        reason = request.POST.get('void_reason', '')
        txn.is_voided = True
        txn.voided_by = request.user
        txn.voided_at = timezone.now()
        txn.void_reason = reason
        txn.save()

        FeeAuditLog.objects.create(
            transaction=txn,
            action='VOIDED',
            done_by=request.user,
            details=f"Voided receipt {txn.receipt_number} - Reason: {reason}"
        )
        messages.success(request, f"Receipt {txn.receipt_number} voided")
        return redirect('accounts:admin_fee_monitoring')

    return render(request, 'accounts/void_confirm.html', {'txn': txn})


@login_required
@require_http_methods(["GET", "POST"])
def mark_teacher_attendance(request):

    if request.user.role not in ["admin", "headmaster"]:
        messages.error(
            request, "You do not have permission to mark teacher attendance."
        )
        return redirect("accounts:home")

    school = request.user.school
    if not school:
        messages.error(request, "Your account is not assigned to any school.")
        return redirect("accounts:home")

    date_value = request.POST.get("attendance_date") or request.GET.get("date")
    attendance_date = parse_date(date_value) if date_value else timezone.localdate()
    if not attendance_date:
        attendance_date = timezone.localdate()

    active_term = Term.objects.filter(school=school, is_active=True).first()
    if not active_term:
        messages.error(request, "There is no active term.")
        return redirect("accounts:home")

    is_weekend = attendance_date.weekday() >= 5

    if (
        attendance_date < active_term.start_date
        or attendance_date > active_term.end_date
    ):
        messages.error(request, "This date is outside the active school term.")
        return redirect(f"{request.path}?date={timezone.localdate()}")

    teachers = User.objects.filter(school=school, role="teacher").order_by(
        "first_name", "last_name"
    )

    # ✅ FIX: Load existing attendance for this date
    existing_qs = TeacherAttendance.objects.filter(school=school, date=attendance_date)
    existing_attendance = {att.teacher_id: att for att in existing_qs}

    if request.method == "POST":
        if attendance_date.weekday() >= 5:
            messages.error(request, "Teacher attendance cannot be marked on weekends.")
            for teacher in teachers:
                teacher.today_attendance = existing_attendance.get(teacher.id)
            return render(
                request,
                "accounts/mark_teacher_attendance.html",
                {
                    "teachers": teachers,
                    "attendance_date": attendance_date,
                    "existing_attendance": existing_attendance,
                    "school": school,
                    "is_weekend": True,
                },
            )

        for teacher in teachers:
            status = request.POST.get(f"status_{teacher.id}")
            if not status:
                continue
            time_in = request.POST.get(f"time_in_{teacher.id}") or None
            note = request.POST.get(f"note_{teacher.id}") or ""

            TeacherAttendance.objects.update_or_create(
                teacher=teacher,
                date=attendance_date,
                defaults={
                    "school": school,
                    "status": status,
                    "time_in": time_in,
                    "note": note,
                    "marked_by": request.user,
                    "record_source": "staff",
                },
            )

        messages.success(
            request, f"Teacher attendance for {attendance_date} saved successfully!"
        )
        return redirect(f"{request.path}?date={attendance_date}")

    # Attach existing to teacher objects for easy template access
    for teacher in teachers:
        teacher.today_attendance = existing_attendance.get(teacher.id)

    return render(
        request,
        "accounts/mark_teacher_attendance.html",
        {
            "teachers": teachers,
            "attendance_date": attendance_date,
            "existing_attendance": existing_attendance,
            "school": school,
            "is_weekend": is_weekend,
        },
    )


@login_required
def edit_teacher_attendance(request, attendance_id):

    if request.user.role not in ['admin', 'headmaster']:
        messages.error(
            request,
            "You do not have permission to edit attendance."
        )
        return redirect("accounts:home")


    school = request.user.school


    attendance = get_object_or_404(
        TeacherAttendance,
        id=attendance_id,
        school=school
    )


    if request.method == "POST":

        attendance.status = request.POST.get("status")

        attendance.marked_by = request.user

        if attendance.status in ["P", "L"]:
            attendance.time_in = request.POST.get("time_in") or None
            attendance.time_out = request.POST.get("time_out") or None
        else:
            attendance.time_in = None
            attendance.time_out = None

        attendance.note = request.POST.get("note") or ""

        attendance.save()

        messages.success(
            request,
            "Teacher attendance updated successfully."
        )

        return redirect("accounts:teacher_attendance_report")


    return render(
        request,
        "accounts/edit_teacher_attendance.html",
        {
            "attendance": attendance,
            "school": school,
        }
    )


@login_required
def my_attendance(request):

    if request.user.role != "teacher":
        messages.error(
            request,
            "You do not have permission to view this page."
        )
        return redirect("accounts:home")

    teacher = request.user
    school = teacher.school
    try:
        active_term = Term.objects.get(
            school=school,
            is_active=True
        )
    except Term.DoesNotExist:
        active_term = None

    week_str = request.GET.get("attendance_week")

    if week_str:
        selected_date = parse_date(week_str)
    else:
        selected_date = timezone.localdate()

    if not selected_date:
        selected_date = timezone.localdate()
    def get_active_week(start_date, current_date):
        if current_date < start_date:
            return 1

        days_diff = (current_date - start_date).days
        start_weekday = start_date.weekday()

        return (days_diff + start_weekday) // 7 + 1


    def get_week_number(start_date, target_date):
        if target_date < start_date:
            return 1

        days_diff = (target_date - start_date).days
        start_weekday = start_date.weekday()

        return (days_diff + start_weekday) // 7 + 1

    # Move weekends to Friday
    if selected_date.weekday() >= 5:
        selected_date = selected_date - timedelta(
            days=selected_date.weekday() - 4
        )

    week_start = selected_date - timedelta(
        days=selected_date.weekday()
    )

    week_end = week_start + timedelta(days=4)
# WEEK NAVIGATION - FIXED
    attendance_previous_week = week_start - timedelta(days=7)
    attendance_next_week = week_start + timedelta(days=7)
    is_first_week = False
    is_last_week = False
    
    if active_term:
        first_week_start = active_term.start_date - timedelta(
            days=active_term.start_date.weekday()
        )
        last_week_start = active_term.end_date - timedelta(
            days=active_term.end_date.weekday()
        )
        
        if week_start <= first_week_start:
            is_first_week = True
            previous_week = week_start  # stay, but button disabled
        if week_start >= last_week_start:
            is_last_week = True
            next_week = week_start  # stay, but button disabled

    current_week_start = timezone.localdate() - timedelta(
        days=timezone.localdate().weekday()
    )

    is_current_week = week_start == current_week_start

    working_days = []

    current = week_start

    while current <= week_end:
        if current.weekday() < 5:
            working_days.append(current)
        current += timedelta(days=1)

    attendance_records = TeacherAttendance.objects.filter(
        teacher=teacher,
        school=school,
        date__range=[week_start, week_end]
    )

    attendance_map = {
        attendance.date: attendance
        for attendance in attendance_records
    }

    days = []

    present = 0
    late = 0
    absent = 0
    sick = 0
    leave = 0
    excused = 0

    for day in working_days:

        attendance = attendance_map.get(day)

        status = None

        if attendance:

            status = attendance.status

            if status == "P":
                present += 1
            elif status == "L":
                late += 1
            elif status == "A":
                absent += 1
            elif status == "S":
                sick += 1
            elif status == "LV":
                leave += 1
            elif status == "E":
                excused += 1

        days.append({
            "date": day,
            "attendance": attendance,
            "status": attendance.status if attendance else None,
            "time_in": attendance.time_in if attendance else None,
            "time_out": attendance.time_out if attendance else None,
            "note": attendance.note if attendance else None,
            "marked_by": attendance.marked_by if attendance else None,
        })

    attendance_rate = 0

    if working_days:
        attendance_rate = round(
            ((present + late) / len(working_days)) * 100,
            1
        )
    attendance_week_number = 1

    if active_term:
        attendance_week_number = get_week_number(
            active_term.start_date,
            selected_date
    )
        print("Selected date:", selected_date)
        print("Attendance week number:", attendance_week_number)

    return render(
        request,
        "accounts/my_attendance.html",
        {
            "working_days": working_days,
            "attendance_days": days,

            "total_present": present,
            "total_late": late,
            "total_absent": absent,
            "total_leave": leave,
            "total_sick": sick,
            "total_excused": excused,
            "attendance_week_number": attendance_week_number,

            "attendance_rate": attendance_rate,

            "attendance_week_start": week_start,
            "attendance_week_end": week_end,

            "attendance_previous_week": attendance_previous_week,
            "attendance_next_week": attendance_next_week,
            "is_current_week": is_current_week,

            "report_week_start": week_start,
            "report_week_end": week_end,
            "is_first_week": is_first_week,
            "is_last_week": is_last_week,
        },
    )


@login_required
def teacher_check_in(request):

    if request.user.role != "teacher":
        messages.error(
            request,
            "Only teachers can check in."
        )
        return redirect("accounts:home")


    school = request.user.school

    today = timezone.localdate()
    current_time = timezone.localtime().time()


    # Get today's attendance
    attendance, created = TeacherAttendance.objects.get_or_create(
        school=school,
        teacher=request.user,
        date=today,
        defaults={
            "status": "P",
            "marked_by": None,
        }
    )


    # Already checked in
    if attendance.time_in:
        messages.info(
            request,
            "You have already checked in today."
        )
        return redirect("accounts:my-attendance")


    # Get school attendance settings
    setting = SchoolSetting.objects.get(
        school=school
    )


    reporting_time = setting.teacher_reporting_time
    late_minutes = setting.late_after_minutes


    # Calculate late time
    late_limit = (
        datetime.combine(
            today,
            reporting_time
        )
        +
        timedelta(
            minutes=late_minutes
        )
    ).time()


    if current_time > late_limit:
        attendance.status = "L"

    else:
        attendance.status = "P"


    attendance.time_in = current_time
    attendance.record_source = "self"

    attendance.save()


    messages.success(
        request,
        "Check-in successful."
    )


    return redirect(
        "accounts:my-attendance"
    )


@login_required
def teacher_check_out(request):

    if request.user.role != "teacher":
        messages.error(
            request,
            "Only teachers can check out."
        )
        return redirect("accounts:home")


    school = request.user.school

    today = timezone.localdate()


    attendance = get_object_or_404(
        TeacherAttendance,
        school=school,
        teacher=request.user,
        date=today
    )


    if attendance.time_out:
        messages.info(
            request,
            "You have already checked out."
        )
        return redirect(
            "accounts:my-attendance"
        )


    attendance.time_out = (
        timezone.localtime().time()
    )

    attendance.save()


    messages.success(
        request,
        "Check-out successful."
    )


    return redirect(
        "accounts:my-attendance"
    )
