# accounts/views.py
# Django imports
import re
import random 
import secrets
import string
import uuid
import io
import json
from math import radians, sin, cos, sqrt, atan2
import zipfile
from django.apps import apps
from django.core import serializers
from .hubtel_service import initiate_hubtel_payment
import base64
import qrcode
from django.db import transaction
from django.core.files.base import ContentFile
from django.utils import timezone
from .utils import get_grading_scale
from .utils import get_weeks_for_term
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

import sqlite3
from django.conf import settings
from django.db.models.functions import Rank, Coalesce
from django.db.models.functions import Rank
from django.http import HttpResponse, Http404, FileResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db import transaction, IntegrityError
from django.template.loader import render_to_string, get_template
from weasyprint import HTML
# from xhtml2pdf import pisa
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
    TermSetting, SystemSetting, DynamicFeeStructure, DynamicFeeStructureItem, STAGE_CHOICES, DynamicStudentFeeItem, HeadmasterPermission, AcademicYear,
    BasicDailyFeeSetting, BasicDailyFeeCollection, GradingScale,
)
from django.db.models import Case, When, Value, IntegerField, CharField, Min

from .forms import (
    ExpenseForm, TeacherForm, AccountantForm, StudentForm, PendingPaymentForm,
    TeacherEditForm, AddSubjectToClassForm, ResultUploadForm, SchoolSmsSettingsForm,
    TermSettingForm, SchoolClassForm, TermForm, AcademicCalendarForm, 
    AccountantEditForm, StudentFeeForm, ResultForm, AssignTeacherForm, HeadmasterForm, HeadmasterEditForm, ParentForm,
    GradingScaleForm,

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

    if user.role in ['admin', 'proprietor', 'proprietress']:
        return redirect('accounts:admin_dashboard')

    elif user.role == 'teacher':
        return redirect('accounts:teacher-dashboard')

    elif user.role == 'student':
        return redirect('accounts:student_dashboard')

    elif user.role == 'parent':
        return redirect('accounts:parent_dashboard')

    elif user.role == 'accountant':
        return redirect('accounts:accountant_dashboard')

    elif user.role in ['headmaster', 'headmistress']:
        return redirect('accounts:headmaster_dashboard')

    else:
        return redirect('accounts:home')

def calculate_grade(total_score, school, term):

    grading = GradingScale.objects.filter(
        school=school,
        term=term,
        min_score__lte=total_score,
        max_score__gte=total_score,
    ).first()

    if not grading:
        grading = GradingScale.objects.filter(
            school=school,
            term__isnull=True,
            min_score__lte=total_score,
            max_score__gte=total_score,
        ).first()

    return grading.grade if grading else "-"


def role_required(allowed_roles=[]):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')

            if request.user.role not in allowed_roles:
                if request.user.role in ['admin', 'proprietor', 'proprietress']:
                    return redirect('accounts:admin_dashboard')

                elif request.user.role == 'teacher':
                    return redirect('accounts:teacher_dashboard')

                elif request.user.role == 'student':
                    return redirect('accounts:student_dashboard')

                elif request.user.role == 'parent':
                    return redirect('accounts:parent_dashboard')

                elif request.user.role == 'accountant':
                    return redirect('accounts:accountant_dashboard')

                elif request.user.role in ['headmaster', 'headmistress']:
                    return redirect('accounts:headmaster_dashboard')

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

    if role in ["admin", "proprietor", "proprietress"]:
        return redirect('accounts:admin_dashboard')

    elif role == "teacher":
        return redirect('teacher-dashboard')

    elif role == "student":
        return redirect('student_dashboard')

    elif role == "parent":
        return redirect('parent_dashboard')

    elif role == "accountant":
        return redirect('accountant_dashboard')
    elif role in ["headmaster", "headmistress"]:
        return redirect('headmaster_dashboard')
    else:

     return redirect('accounts:login')


@login_required
def admin_dashboard(request):

    if request.user.role not in ["admin", "proprietor", "proprietress"]:
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
        # ============================
        # ACADEMIC YEAR & CURRENT TERM
        # ============================

        academic_year = AcademicYear.objects.filter(
            school=school,
            is_active=True
        ).first()

        current_term = Term.objects.filter(
            school=school,
            is_active=True
        ).first()

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
        "academic_year": academic_year,
        "current_term": current_term,

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
    if request.user.role != "student":
        return redirect_to_dashboard(request.user)

    student = request.user
    # EXTRA SAFETY: Ensure student has school and class
    if not student.school:
        return redirect("login")  # or error page

    current_term = (
        Term.objects.filter(school=student.school, is_active=True)
        .select_related("academic_year")
        .first()
    )

    if not current_term:
        return render(
            request,
            "accounts/student_dashboard.html",
            {
                "student": student,
                "student_class": student.school_class,
                "current_term": None,
                "total_fees": 0,
                "balance": 0,
                "average_score": 0,
                "total_subjects": 0,
                "attendance_percent": 0,
                "attendance_percent_text": "0%",
                "today_timetable": [],
                "announcements": [],
                "announcement_count": 0,
                "term_grade": "-",
                "term_remark": "No results",
            },
        )

    student_results = Result.objects.filter(
        school=student.school,  # <-- ADDED school isolation
        student=student,
        term=current_term,
        academic_year=current_term.academic_year,
        status="published",
    )

    totals = [r.total_score or 0 for r in student_results]
    avg_score = sum(totals) / len(totals) if totals else 0
    total_subjects = student_results.count()

    def get_term_grading(pct):
        grading = GradingScale.objects.filter(
            school=student.school,
            term=current_term,
            min_score__lte=pct,
            max_score__gte=pct,
        ).first()

        if not grading:
            grading = GradingScale.objects.filter(
                school=student.school,
                term__isnull=True,
                min_score__lte=pct,
                max_score__gte=pct,
            ).first()

        return grading


    term_grading = get_term_grading(avg_score)

    term_grade = term_grading.grade if term_grading and total_subjects > 0 else "-"
    term_remark = (
        term_grading.remark
        if term_grading and total_subjects > 0
        else "No results"
    )



    today_date = timezone.now().date()

    spent_days = calculate_school_days(
        current_term.start_date, today_date, student.school
    )

    total_days = calculate_school_days(
        current_term.start_date, current_term.end_date, student.school
    )

    attendance_records = AttendanceRecord.objects.filter(
        school=student.school,  # <-- ADDED
        student=student,
        session__school=student.school,  # <-- ADDED double protection
        session__date__gte=current_term.start_date,
        session__date__lte=today_date,
    ).values("session__date", "status")

    daily_statuses = defaultdict(list)
    for record in attendance_records:
        daily_statuses[record["session__date"]].append(record["status"])

    present_days = 0
    for statuses in daily_statuses.values():
        if "P" in statuses:
            present_days += 1

    attendance_percent = (
        round((present_days / spent_days) * 100, 1) if spent_days else 0
    )
    attendance_display = f"{present_days}/{spent_days}"

    # NOTE: StudentFee academic_year in your model is CharField, but current_term.academic_year is FK
    # So we filter by name
    fee_record = StudentFee.objects.filter(
        school=student.school,  # <-- ADDED
        student=student,
        term=current_term,
        academic_year=str(current_term.academic_year),  # <-- FIX for CharField vs FK
    ).first()

    today = timezone.now().date()
    weekday_int = today.weekday()
    today_timetable = []
    if weekday_int <= 4:
        today_timetable = (
            Timetable.objects.filter(
                school=student.school,
                school_class=student.school_class,
                weekday=weekday_int,
            )
            .select_related("subject", "teacher")
            .order_by("start_time")
        )

    all_announcements = Announcement.objects.filter(
        school=student.school, is_active=True
    ).order_by("-created_at")

    filtered = []
    for ann in all_announcements:
        roles = [r.lower().replace("_", " ") for r in (ann.target_roles or [])]
        if isinstance(roles, str):
            roles = (
                roles.lower()
                .replace("'", "")
                .replace("[", "")
                .replace("]", "")
                .split(",")
            )

        roles = [r.strip().replace("_", " ") for r in roles]

        if "all" in roles or "student" in roles or "students" in roles:
            filtered.append(ann)
        elif "specific class" in roles:
            if ann.target_classes.filter(id=student.school_class_id).exists():
                filtered.append(ann)
            elif ann.target_class_id and ann.target_class_id == student.school_class_id:
                filtered.append(ann)

    announcement_count = len(filtered)
    announcements = filtered[:5]

    context = {
        "student": student,
        "student_class": student.school_class,
        "current_term": current_term,
        "average_score": round(avg_score, 1),
        "total_subjects": total_subjects,
        "attendance_percent": attendance_percent,
        "attendance_percent_text": f"{attendance_percent}%",
        "total_fees": fee_record.total_amount if fee_record else 0,
        "balance": fee_record.balance() if fee_record else 0,
        "fee_record": fee_record,
        "results": student_results,
        "today_timetable": today_timetable,
        "announcements": announcements,
        "announcement_count": announcement_count,
        "term_grade": term_grade,
        "term_remark": term_remark,
        "present_days": present_days,
        "spent_days": spent_days,
        "total_days": total_days,
        "attendance_display": attendance_display,
    }
    return render(request, "accounts/student_dashboard.html", context)


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
        if user.role == "student":
            school_login = user.school.allows_student_login
            class_login = user.school_class.allows_student_login

            if not school_login or not class_login or not user.can_login:
                messages.error(request, "Student login is not enabled for your school or class.")
                return render(request, "accounts/login.html")
        authenticated_user = authenticate(
            request,
            username=user.username,
            password=password
        )
        if authenticated_user:
            login(request, authenticated_user)
            if authenticated_user.is_superuser:
                return redirect('/admin/')
            if not authenticated_user.is_password_changed:
                return redirect('accounts:change-password')
            if authenticated_user.role in ["admin", "proprietor", "proprietress"]:
                return redirect('accounts:admin_dashboard')
            elif authenticated_user.role == "teacher":
                return redirect('accounts:teacher-dashboard')
            elif authenticated_user.role == "student":
                return redirect('accounts:student_dashboard')
            elif authenticated_user.role == "accountant":
                return redirect('accounts:accountant_dashboard')
            elif authenticated_user.role == "parent":   
                return redirect('accounts:parent_dashboard')  
            elif authenticated_user.role in ["headmaster", "headmistress"]:  
                return redirect('accounts:headmaster_dashboard')  
            return redirect('accounts:dashboard')  
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
        elif user.role in ['headmaster', 'headmistress']:
            return redirect('accounts:headmaster_dashboard')
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

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect("accounts:dashboard")

    role = request.user.role

    if role in ["admin", "proprietor", "proprietress"]:
        pass
    elif role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "You don't have permission")
                return redirect("accounts:headmaster_dashboard")
        except Exception:
            messages.error(request, "No permission set")
            return redirect("accounts:headmaster_dashboard")
    else:
        return redirect("accounts:dashboard")

    if request.method != "POST":
        return redirect("accounts:manage-students")

    student = get_object_or_404(
        User, id=student_id, role="student", school=request.user.school
    )

    student.can_login = not student.can_login
    student.save()

    if student.can_login:
        messages.success(request, f"{student.get_full_name()} can now log in.")
    else:
        messages.success(
            request, f"{student.get_full_name()} has been blocked from logging in."
        )

    return redirect(request.META.get("HTTP_REFERER", "accounts:manage-students"))


# ----------------------------
# DASHBOARD
# ----------------------------
@login_required
def dashboard(request):
    school = request.user.school

    if request.user.role in ["admin", "proprietor", "proprietress"]:
        context = {
            "total_students": User.objects.filter(
                role="student", school=school
            ).count(),
            "total_teachers": User.objects.filter(
                role="teacher", school=school
            ).count(),
            "total_parents": User.objects.filter(role="parent", school=school).count(),
            "total_users": User.objects.filter(school=school).count(),
        }
        return render(request, "accounts/admin_dashboard.html", context)
    elif request.user.role in ["headmaster", "headmistress"]:
        context = {
            "total_students": User.objects.filter(
                role="student", school=school
            ).count(),
            "total_teachers": User.objects.filter(
                role="teacher", school=school
            ).count(),
            "total_parents": User.objects.filter(role="parent", school=school).count(),
            "total_users": User.objects.filter(school=school).count(),
        }
        return render(request, "accounts/headmaster_dashboard.html", context)
    elif request.user.role == "teacher":
        return render(request, "accounts/teacher_dashboard.html")
    elif request.user.role == "student":
        return render(request, "accounts/student_dashboard.html")
    elif request.user.role == "parent":
        return render(request, "accounts/parent_dashboard.html")
    return render(request, "accounts/dashboard_base.html")


# ----------------------------
# ADMIN VIEWS
# --------------------------------


@login_required
def manage_students(request, class_id=None):
    credentials = request.session.pop("credentials", None)

    show_credentials = False

    if credentials:
        student = User.objects.filter(
            id=credentials.get("student_id"),
            role="student",
            school=request.user.school
        ).select_related("school_class").first()

        if student and student.school_class:
            show_credentials = (
                student.school.allows_student_login
                and student.school_class.allows_student_login
            )

    # --- ONLY THIS PART WE ADD - Regardez ma permission ---
    if not request.user.school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')

    role = request.user.role
    if role in ['admin', 'proprietor', 'proprietress']:
        allowed_class_ids = SchoolClass.objects.filter(
            school=request.user.school
        ).values_list('id', flat=True)
    elif role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "You don't have permission to manage students")
                return redirect('accounts:headmaster_dashboard')
            # headmaster can see all classes like admin
            allowed_class_ids = SchoolClass.objects.filter(
                school=request.user.school
            ).values_list('id', flat=True)
        except Exception:
            messages.error(request, "Permission not set")
            return redirect('accounts:headmaster_dashboard')
    else:  # teacher
        allowed_class_ids = TeacherSubjectClass.objects.filter(
            school_class__school=request.user.school,
            teacher=request.user
        ).values_list('school_class_id', flat=True).distinct()
    # --- END OF PERMISSION PART - Everything below is YOUR OLD CODE 100% same ---

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

    if request.user.role in ['admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress']:
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
        'credentials': credentials,
        'show_credentials': show_credentials

    }
    return render(request, "accounts/manage_students.html", context)

@login_required
def manage_students_grid(request, class_id=None):
    credentials = request.session.pop("credentials", None)
    show_credentials = False

    if credentials:
        student = User.objects.filter(
            id=credentials.get("student_id"),
            role="student",
            school=request.user.school
        ).select_related("school_class").first()

        if student and student.school_class:
            show_credentials = (
                student.school.allows_student_login
                and student.school_class.allows_student_login
            )

    # --- ADD PERMISSION ONLY ---
    if not request.user.school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
    
    role = request.user.role
    if role in ['admin', 'proprietor', 'proprietress']:
        allowed_class_ids = SchoolClass.objects.filter(
            school=request.user.school
        ).values_list('id', flat=True)
    elif role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "You don't have permission")
                return redirect('accounts:headmaster_dashboard')
            allowed_class_ids = SchoolClass.objects.filter(
                school=request.user.school
            ).values_list('id', flat=True)
        except Exception:
            messages.error(request, "Permission not set")
            return redirect('accounts:headmaster_dashboard')
    else:  
        allowed_class_ids = TeacherSubjectClass.objects.filter(
            school_class__school=request.user.school,
            teacher=request.user
        ).values_list('school_class_id', flat=True).distinct()
    # --- END PERMISSION ---

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
    
    if request.user.role in ['admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress']:
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
        'credentials': credentials,
        'show_credentials': show_credentials

    }
    return render(request, "accounts/manage_students.html", context)


@login_required
def assign_student_subject(request, student_id):

    school = request.user.school

    # --- ADD PERMISSION ONLY ---
    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')

    role = request.user.role
    if role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif role in ['headmaster', 'headmistress']:
        try:
            hp = school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "You don't have permission")
                return redirect('accounts:headmaster_dashboard')
        except Exception:
            messages.error(request, "Permission not set")
            return redirect('accounts:headmaster_dashboard')
    else:
        messages.error(request, "Not allowed")
        return redirect('accounts:dashboard')
    # --- END PERMISSION ---

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

    # --- ADD PERMISSION ONLY ---
    if not school:
        messages.error(request, "No school assigned")
        return redirect("accounts:dashboard")

    role = request.user.role
    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass
    elif role in ["headmaster", "headmistress"]:
        try:
            hp = school.headmaster_permissions
            # for teachers we use can_add_teacher permission
            if not hp.can_add_teacher:
                messages.error(request, "You don't have permission")
                return redirect("accounts:headmaster_dashboard")
        except Exception:
            messages.error(request, "Permission not set")
            return redirect("accounts:headmaster_dashboard")
    else:
        messages.error(request, "Not allowed")
        return redirect("accounts:dashboard")
    # --- END PERMISSION ---

    classes = (
        SchoolClass.objects.filter(school=school)
        .prefetch_related(
            Prefetch(
                "teachersubjectclass_set",
                queryset=TeacherSubjectClass.objects.select_related(
                    "subject", "teacher"
                ).filter(teacher__school=school),
            )
        )
        .filter(teachersubjectclass__isnull=False)
        .distinct()
        .order_by("name")
    )

    return render(request, "accounts/assigned_teachers_list.html", {"classes": classes})


@login_required
def add_student(request):
    credentials = None

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect("accounts:dashboard")

    # --- PERMISSION ---
    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass  # admin-level roles allowed always

    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "Admin has not allowed you to add students")
                return redirect("accounts:headmaster_dashboard")
        except HeadmasterPermission.DoesNotExist:
            messages.error(request, "No permissions set by admin yet")
            return redirect("accounts:headmaster_dashboard")

    elif request.user.role == "teacher":
        if not request.user.school.can_teachers_manage_students:
            messages.error(
                request,
                "Your school admin has disabled teacher access to add students.",
            )
            return redirect("accounts:teacher_dashboard")

    else:
        return redirect("accounts:dashboard")
    # --- END PERMISSION ---

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
                'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
            ) for _ in range(10))

            student.set_password(password)

            student.can_login = (student.school.allows_student_login and student.school_class.allows_student_login)

            student.save()

            credentials = {
                "username": student.username,
                "student_number": student.student_number,
                "password": password,
                "student_id": student.id
            }
            request.session["credentials"] = credentials
            request.session["last_student_id"] = student.id

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
    school = request.user.school
    
    # --- ADD PERMISSION + ISOLATION ONLY ---
    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
    
    role = request.user.role
    if role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif role in ['headmaster', 'headmistress']:
        try:
            hp = school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "You don't have permission")
                return redirect('accounts:headmaster_dashboard')
        except Exception:
            messages.error(request, "Permission not set")
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')

    # Isolation fix - assignment must be from your school
    assignment = get_object_or_404(
        StudentSubjectClass, 
        id=id,
        student__school=school  # <-- isolation
    )
    classes = SchoolClass.objects.filter(school=school)
    subjects = Subject.objects.filter(school=school)
    # --- END FIX ---
    
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

    elif request.user.role not in ["admin", "proprietor", "proprietress", "teacher"]:
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
        student.conduct = request.POST.get("conduct", student.conduct)

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
        User, id=student_id, role="student", school=request.user.school
    )

    # --- ADD PERMISSION ONLY ---
    role = request.user.role
    if role in ["admin", "proprietor", "proprietress"]:
        pass
    elif role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            # headmaster can view if he can manage students OR can view dashboard
            if not (hp.can_add_student or hp.can_view_dashboard):
                messages.error(request, "No permission to view students")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif role == "teacher":
        # your old teacher check will still run below in other logic, we keep it
        pass
    elif role == "student" and request.user.id != student.id:
        return redirect("accounts:student_dashboard")
    elif role not in ["admin","proprietor", "proprietress", "headmaster", "headmistress", "teacher", "student"]:
        return redirect("accounts:dashboard")
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
    # FEES INFORMATION - DYNAMIC ONLY - FIXED
    # ==========================
    # Get ALL fees for this student - no filter for now to find paid one
    student_fees = StudentFee.objects.filter(
        student=student, school=request.user.school
    ).order_by("-id")

    # Only filter if user selected term/year
    term_id = request.GET.get("term")
    year_id = request.GET.get("year")

    if term_id and selected_term:
        student_fees = student_fees.filter(term=selected_term)
    if year_id and selected_year:
        student_fees = student_fees.filter(academic_year=selected_year.name)

    # Build breakdown from DynamicStudentFeeItem ONLY
    fee_breakdowns = []
    for fee in student_fees:
        breakdown = []
        for item in fee.dynamic_items.all():  # <-- DYNAMIC ITEMS
            breakdown.append(
                {
                    "name": item.name,
                    "amount": item.amount_due,
                    "paid": item.amount_paid,
                    "balance": item.balance,
                }
            )
        fee_breakdowns.append(
            {
                "fee": fee,
                "items": breakdown,
                "total_amount": fee.total_amount,
                "total_paid": fee.amount_paid(),
                "total_balance": fee.balance(),
            }
        )

    # Payment history stays same but school isolated
    payment_history = PaymentTransaction.objects.filter(
        student=student, school=request.user.school, is_voided=False
    )
    if selected_term:
        payment_history = payment_history.filter(term=selected_term)
    if selected_year:
        payment_history = payment_history.filter(academic_year=selected_year)

    payment_history = payment_history.prefetch_related("items").order_by("-created_at")
    payment_history = Paginator(payment_history, 10).get_page(
        request.GET.get("payment_page")
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

    # --- ADD PERMISSION ONLY ---
    if not request.user.school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
    
    role = request.user.role
    if role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_student: # we use can_add_student for delete too
                messages.error(request, "You don't have permission to delete students")
                return redirect('accounts:headmaster_dashboard')
        except Exception:
            messages.error(request, "Permission not set")
            return redirect('accounts:headmaster_dashboard')
    else:
        messages.error(request, "Not allowed")
        return redirect('accounts:dashboard')
    # --- END PERMISSION ---

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
    # --- FIXED PERMISSION - was only admin, now admin + headmaster ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_student:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')  # fixed typo
    # --- END FIX ---

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
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    else:
        return redirect("accounts:dashboard")
    # --- END FIX ---

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

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect("accounts:home")

    # --- FIX: allow headmaster with permission ---
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "Admin has not allowed you to add teachers")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect("accounts:home")

    # ... keep rest of your code exactly ...

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
        User, id=teacher_id, role="teacher", school=request.user.school
    )

    # --- ADD PERMISSION ONLY ---
    role = request.user.role
    if role in ["admin", "proprietor", "proprietress"]:
        pass
    elif role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission to view teachers")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif role != "teacher":  # teacher can view other teachers? keep your logic
        pass

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

    # --- ADD PERMISSION ONLY ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission to edit teachers")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')

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
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission to delete teachers")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    

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
    # --- FIXED PERMISSION - FIRST ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END FIX ---

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
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END FIX ---

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
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END FIX ---

 

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
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_manage_assignments:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
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
    # --- PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_view_attendance:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END PERMISSION ---

    today = timezone.now().date()

    # --- FIXED ISOLATION ---
    total_students = User.objects.filter(
        role='student',
        school=request.user.school,
        is_active=True
    ).count()

    present_today = Attendance.objects.filter(
        date=today,
        status="Present",
        student__school=request.user.school
    ).count()

    absent_today = Attendance.objects.filter(
        date=today,
        status="Absent",
        student__school=request.user.school
    ).count()
    # --- END FIX ---

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

    # Admin / Proprietor
    if request.user.role in ["admin", "proprietor", "proprietress"]:
        users = User.objects.filter(school=request.user.school).exclude(
            role__in=["student", "teacher"]
        )

    # Basic School Headmaster / Headmistress
    elif request.user.role in ["headmaster", "headmistress"]:

        school = request.user.school

        if not school or school.edition != "basic":
            return redirect("accounts:dashboard")

        users = User.objects.filter(school=school).exclude(
            role__in=["parent", "accountant", "student"]
        )

    else:
        return redirect("accounts:dashboard")

    return render(request, "accounts/manage_users.html", {"users": users})


@login_required
def edit_user(request, user_id):
    if request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect("accounts:dashboard")

    # --- FIXED ISOLATION ---
    user = get_object_or_404(User, id=user_id, school=request.user.school)
    roles = [
        "admin",
        "headmaster",
        "teacher",
        "student",
        "parent",
        "accountant",
    ]  # add your roles

    if request.method == "POST":
        user.username = request.POST.get("username", user.username)
        user.email = request.POST.get("email", user.email)
        # prevent role change to admin if you want
        new_role = request.POST.get("role", user.role)
        if new_role in roles:
            user.role = new_role
        user.save()
        messages.success(request, f"{user.username} updated successfully!")
        return redirect("accounts:manage-users")

    return render(request, "accounts/edit_user.html", {"user": user, "roles": roles})


@login_required
def delete_user(request, user_id):
    if request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect("accounts:dashboard")

    # --- FIXED ISOLATION + PREVENT SELF DELETE ---
    user = get_object_or_404(User, id=user_id, school=request.user.school)

    if user.id == request.user.id:
        messages.error(request, "You cannot delete yourself!")
        return redirect("accounts:manage-users")

    user.delete()
    messages.success(request, f"{user.username} deleted successfully!")
    return redirect("accounts:manage-users")


@login_required
def manage_parents(request):
    # --- FIXED PERMISSION + SCHOOL ISOLATION ---
    if not hasattr(request.user, "school") or request.user.school is None:
        messages.error(request, "No school assigned")
        return redirect("accounts:dashboard")

    school = request.user.school

    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass
    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = school.headmaster_permissions
            if hasattr(hp, "can_add_parent"):
                if not hp.can_add_parent:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
            else:
                if not hp.can_add_student:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    else:
        return redirect("accounts:dashboard")
    # --- END FIX ---

    # ✅ GET CREDENTIALS FROM ADD PARENT REDIRECT
    credentials = request.session.pop("parent_credentials", None)

    parents = User.objects.filter(role="parent", is_active=True, school=school)

    search_query = request.GET.get("q", "")
    if search_query:
        name_parts = search_query.strip().split()
        if len(name_parts) == 1:
            parents = parents.filter(
                Q(first_name__icontains=name_parts[0])
                | Q(last_name__icontains=name_parts[0])
            )
        else:
            parents = parents.filter(
                Q(first_name__icontains=name_parts[0])
                & Q(last_name__icontains=name_parts[-1])
            )

    paginator = Paginator(parents, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "accounts/manage_parents.html",
        {
            "parents": page_obj,
            "search_query": search_query,
            "credentials": credentials,  # ✅ This shows green box on manage page
        },
    )


@login_required
def add_parent(request):
    if not hasattr(request.user, "school") or request.user.school is None:
        messages.error(request, "No school assigned to your account")
        return redirect("accounts:dashboard")

    school = request.user.school

    # --- PERMISSIONS ---
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = school.headmaster_permissions

            if not hp.can_add_parent:
                messages.error(
                    request,
                    "Admin has not allowed you to add parents"
                )
                return redirect("accounts:headmaster_dashboard")

        except:
            return redirect("accounts:headmaster_dashboard")

    elif request.user.role not in [
        "admin",
        "proprietor",
        "proprietress"
    ]:
        return redirect("accounts:dashboard")

    # --- CREATE PARENT ---
    if request.method == "POST":

        first_name = request.POST.get(
            "first_name", ""
        ).strip()

        middle_name = request.POST.get(
            "middle_name", ""
        ).strip()

        last_name = request.POST.get(
            "last_name", ""
        ).strip()

        email = request.POST.get(
            "email", ""
        ).strip()

        phone = request.POST.get(
            "phone", ""
        ).strip()

        occupation = request.POST.get(
            "occupation", ""
        ).strip()

        address = request.POST.get(
            "address", ""
        ).strip()

        relationship = request.POST.get(
            "relationship", ""
        ).strip()

        title = request.POST.get(
            "title", ""
        ).strip()

        # --- EMERGENCY CONTACT ---
        emergency_contact_name = request.POST.get(
            "emergency_contact_name", ""
        ).strip()

        emergency_contact_phone = request.POST.get(
            "emergency_contact_phone", ""
        ).strip()

        emergency_contact_relationship = request.POST.get(
            "emergency_contact_relationship", ""
        ).strip()

        # --- REQUIRED FIELDS ---
        if not first_name or not last_name:
            messages.error(
                request,
                "First name and Last name are required"
            )
            return redirect("accounts:add-parent")

        # --- USERNAME ---
        base_username = (
            f"{first_name}.{last_name}"
            .lower()
            .replace(" ", "")
        )

        username = base_username
        counter = 1

        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        # --- PASSWORD ---
        import secrets
        import string

        password = "".join(
            secrets.choice(
                string.ascii_letters + string.digits
            )
            for _ in range(10)
        )

        # --- CREATE USER ---
        parent = User.objects.create_user(
            username=username,
            password=password,

            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,

            title=title,

            email=email,
            phone=phone,

            role="parent",
            school=school,

            occupation=occupation,
            address=address,
            relationship_type=relationship,

            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
            emergency_contact_relationship=(
                emergency_contact_relationship
            ),
        )

        # --- PHOTO ---
        if request.FILES.get("photo"):
            parent.photo = request.FILES["photo"]
            parent.save()

        # --- SAVE CREDENTIALS ---
        request.session["parent_credentials"] = {
            "username": username,
            "password": password,
            "name": parent.get_full_name(),
        }

        messages.success(
            request,
            f"Parent {parent.get_full_name()} created successfully!"
        )

        return redirect(
            "accounts:manage-parents"
        )

    form = ParentForm()

    return render(
        request,
        "accounts/add_parent.html",
        {
            "form": form
        }
    )


@login_required
def view_parent(request, parent_id):
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if hasattr(hp, 'can_add_parent'):
                if not hp.can_add_parent:
                    messages.error(request, "No permission")
                    return redirect('accounts:headmaster_dashboard')
            else:
                if not hp.can_add_student:
                    messages.error(request, "No permission")
                    return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END FIX ---

    parent = get_object_or_404(User, id=parent_id, role='parent', school=request.user.school)

    children = parent.student_links.select_related('student')

    return render(request, 'accounts/view_parent.html', {
        'parent': parent,
        'children': children
    })


@login_required
def edit_parent(request, parent_id):
    # --- PERMISSION ---
    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass

    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions

            if hasattr(hp, "can_add_parent"):
                if not hp.can_add_parent:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
            else:
                if not hp.can_add_student:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")

        except:
            return redirect("accounts:headmaster_dashboard")

    else:
        return redirect("accounts:dashboard")

    # --- SCHOOL ISOLATION ---
    parent = get_object_or_404(
        User,
        id=parent_id,
        role="parent",
        school=request.user.school
    )

    # --- UPDATE ---
    if request.method == "POST":

        parent.title = request.POST.get(
            "title", ""
        ).strip()

        parent.first_name = request.POST.get(
            "first_name", ""
        ).strip()

        parent.middle_name = request.POST.get(
            "middle_name", ""
        ).strip()

        parent.last_name = request.POST.get(
            "last_name", ""
        ).strip()

        parent.email = request.POST.get(
            "email", ""
        ).strip()

        parent.phone = request.POST.get(
            "phone", ""
        ).strip()

        parent.occupation = request.POST.get(
            "occupation", ""
        ).strip()

        parent.relationship_type = request.POST.get(
            "relationship", ""
        ).strip()

        parent.address = request.POST.get(
            "address", ""
        ).strip()

        # --- EMERGENCY CONTACT ---
        parent.emergency_contact_name = request.POST.get(
            "emergency_contact_name", ""
        ).strip()

        parent.emergency_contact_phone = request.POST.get(
            "emergency_contact_phone", ""
        ).strip()

        parent.emergency_contact_relationship = request.POST.get(
            "emergency_contact_relationship", ""
        ).strip()

        # --- PHOTO ---
        if "photo" in request.FILES:
            parent.photo = request.FILES["photo"]

        parent.save()

        messages.success(
            request,
            "Parent updated successfully!"
        )

        return redirect(
            "accounts:view_parent",
            parent_id=parent.id
        )

    return render(
        request,
        "accounts/edit_parent.html",
        {
            "parent": parent
        }
    )


@login_required
def delete_parent(request, parent_id):

    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass
    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if hasattr(hp, "can_add_parent"):
                if not hp.can_add_parent:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
            else:
                if not hp.can_add_student:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    else:
        return redirect("accounts:dashboard")

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

    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass
    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if hasattr(hp, "can_add_parent"):
                if not hp.can_add_parent:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
            else:
                if not hp.can_add_student:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    else:
        return redirect("accounts:dashboard")

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

    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass
    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if hasattr(hp, "can_add_parent"):
                if not hp.can_add_parent:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
            else:
                if not hp.can_add_student:
                    messages.error(request, "No permission")
                    return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    else:
        return redirect("accounts:dashboard")

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
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_manage_results:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END FIX ---

    if request.method == 'POST':
        form = ResultForm(request.POST)
        # --- FIXED ISOLATION ---
        form.fields['student'].queryset = User.objects.filter(role='student', school=request.user.school)
        if 'subject' in form.fields:
            form.fields['subject'].queryset = Subject.objects.filter(school=request.user.school)
        # --- END FIX ---
        if form.is_valid():
            result = form.save(commit=False)
            # ensure school isolation if model has school
            if hasattr(result, 'school'):
                result.school = request.user.school
            result.save()
            return redirect('accounts:dashboard')
    else:
        form = ResultForm()
        form.fields['student'].queryset = User.objects.filter(role='student', school=request.user.school)
        if 'subject' in form.fields:
            form.fields['subject'].queryset = Subject.objects.filter(school=request.user.school)

    return render(request, 'accounts/add_result.html', {'form': form})

@login_required
def assign_subject(request, teacher_id):
    # --- FIXED PERMISSION ---
    if request.user.role in ['admin', 'proprietor', 'proprietress']:
        pass
    elif request.user.role in ['headmaster', 'headmistress']:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_teacher:
                messages.error(request, "No permission")
                return redirect('accounts:headmaster_dashboard')
        except:
            return redirect('accounts:headmaster_dashboard')
    else:
        return redirect('accounts:dashboard')
    # --- END FIX ---

    # --- FIXED ISOLATION ---
    teacher = get_object_or_404(User, id=teacher_id, role='teacher', school=request.user.school)
    subjects = Subject.objects.filter(school=request.user.school)

    if request.method == 'POST':
        subject_id = request.POST.get('subject_id')
        subject = get_object_or_404(Subject, id=subject_id, school=request.user.school)
        teacher.subjects.add(subject)
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
@role_required(["teacher"])
def teacher_dashboard(request):
    teacher = request.user
    school = teacher.school

    if not school:
        messages.error(request, "Your account is not assigned to any school.")
        return redirect("accounts:home")

    setting = SchoolSetting.objects.filter(school=school).first()
    attendance_mode = setting.attendance_mode if setting else "subject"
    real_today = timezone.now().date()

    today_attendance = TeacherAttendance.objects.filter(
        teacher=teacher, school=school, date=real_today
    ).first()

    can_check_in = False
    can_check_out = False
    if not today_attendance:
        can_check_in = True
    elif today_attendance.time_in and not today_attendance.time_out:
        can_check_out = True

    try:
        week_offset = int(request.GET.get("week", "0") or 0)
    except ValueError:
        week_offset = 0

    monday = (
        real_today - timedelta(days=real_today.weekday()) + timedelta(weeks=week_offset)
    )
    friday = monday + timedelta(days=4)
    school_days = [monday + timedelta(days=i) for i in range(5)]

    # ✅ Added school isolation here
    teacher_weekdays = set(
        Timetable.objects.filter(
            teacher=teacher, school=school, weekday__in=range(5)
        ).values_list("weekday", flat=True)
    )

    scheduled_dates = [d for d in school_days if d.weekday() in teacher_weekdays]
    total_scheduled_this_week = len(scheduled_dates)

    # ✅ Added school isolation here
    teacher_sessions = AttendanceSession.objects.filter(
        teacher=teacher, school=school, date__in=scheduled_dates
    )
    marked_sessions_this_week = (
        teacher_sessions.filter(records__isnull=False).distinct().count()
    )
    last_marked_session = (
        teacher_sessions.filter(records__isnull=False).order_by("-date").first()
    )
    last_marked_day_name = (
        last_marked_session.date.strftime("%A").upper() if last_marked_session else ""
    )
    pending_sessions_this_week = total_scheduled_this_week - marked_sessions_this_week

    display_date = None
    for weekday in range(4, -1, -1):
        if weekday in teacher_weekdays:
            candidate_date = monday + timedelta(days=weekday)
            if monday <= candidate_date <= friday:
                display_date = candidate_date
                break
    if display_date is None:
        display_date = friday

    if (
        week_offset == 0
        and real_today.weekday() < 5
        and real_today.weekday() in teacher_weekdays
    ):
        display_date = real_today
        display_date_label = "Today"
    else:
        display_date_label = display_date.strftime("%A")

    def get_day_suffix(day):
        if 11 <= day <= 13:
            return "th"
        elif day % 10 == 1:
            return "st"
        elif day % 10 == 2:
            return "nd"
        elif day % 10 == 3:
            return "rd"
        else:
            return "th"

    date_range = f"{monday.strftime('%b')} {monday.day}{get_day_suffix(monday.day)} - {friday.strftime('%b')} {friday.day}{get_day_suffix(friday.day)}"

    if week_offset == 0:
        week_label = "This Week"
    elif week_offset == -1:
        week_label = "Last Week"
    elif week_offset == 1:
        week_label = "Next Week"
    else:
        week_label = f"{abs(week_offset)} Weeks {'Ago' if week_offset < 0 else 'Ahead'}"

    assignments = TeacherSubjectClass.objects.filter(
        teacher=teacher, school=school
    ).select_related("school_class", "subject")
    teacher_classes = assignments.values_list("school_class", flat=True)
    teacher_subjects = assignments.values_list("subject", flat=True)

    students_count = (
        User.objects.filter(
            role="student", school=school, school_class_id__in=teacher_classes
        )
        .distinct()
        .count()
    )
    classes_count = assignments.values("school_class").distinct().count()
    subjects_count = assignments.values("subject").distinct().count()

    base_results = Result.objects.filter(
        school=school,
        student__school=school,
        student__school_class_id__in=teacher_classes,
        subject_id__in=teacher_subjects,
    )
    draft_count = base_results.filter(status__in=["draft", "returned"]).count()
    submitted_count = base_results.filter(status="submitted").count()
    returned_count = base_results.filter(status="returned").count()

    timetable_today = Timetable.objects.filter(
        teacher=teacher, school=school, weekday=display_date.weekday()
    )
    total_classes_today = timetable_today.count()

    marked_today = (
        AttendanceSession.objects.filter(
            teacher=teacher, school=school, date=display_date, records__isnull=False
        )
        .distinct()
        .count()
    )

    pending_today = total_classes_today - marked_today

    week_records = AttendanceRecord.objects.filter(
        school=school,
        session__teacher=teacher,
        session__school=school,
        session__date__in=school_days,
    )
    total_week = week_records.exclude(status="excuse").count()
    present_week = week_records.filter(status__in=["present", "late"]).count()
    absent_week = week_records.filter(status="absent").count()
    late_week = week_records.filter(status="late").count()
    excused_week = week_records.filter(status="excuse").count()
    present_percent = (
        round((present_week / total_week * 100), 1) if total_week > 0 else 0
    )

    current_hour = timezone.localtime().hour
    greeting = (
        "Good Morning"
        if current_hour < 12
        else "Good Afternoon" if current_hour < 14 else "Good Evening"
    )

    return render(
        request,
        "accounts/teacher_dashboard.html",
        {
            "assignments": assignments,
            "students_count": students_count,
            "classes_count": classes_count,
            "subjects_count": subjects_count,
            "draft_count": draft_count,
            "submitted_count": submitted_count,
            "returned_count": returned_count,
            "marked_today": marked_today,
            "total_classes_today": total_classes_today,
            "pending_today": pending_today,
            "present_percent": present_percent,
            "present_week": present_week,
            "absent_week": absent_week,
            "late_week": late_week,
            "excused_week": excused_week,
            "total_week": total_week,
            "marked_sessions_this_week": marked_sessions_this_week,
            "last_marked_day_name": last_marked_day_name,
            "total_scheduled_this_week": total_scheduled_this_week,
            "date_range": date_range,
            "week_label": week_label,
            "week_offset": week_offset,
            "display_date_label": display_date_label,
            "today": real_today,
            "attendance_mode": attendance_mode,
            "is_class_teacher": teacher.is_class_teacher,
            "pending_sessions_this_week": pending_sessions_this_week,
            "school_setting": setting,
            "today_attendance": today_attendance,
            "can_check_in": can_check_in,
            "can_check_out": can_check_out,
            "greeting": greeting,
            "today_date": timezone.localdate(),
        },
    )


@role_required(["teacher"])
def teacher_timetable(request):
    teacher = request.user
    school = teacher.school

    if not school:
        messages.error(request, "No school assigned")
        return redirect("accounts:dashboard")

    today_date = timezone.now().date()
    today_weekday = today_date.weekday()

    # =====================================================
    # YEAR & TERM FILTER
    # =====================================================

    selected_year = request.GET.get("year", "")
    selected_term = request.GET.get("term", "")
    selected_term_obj = None

    if selected_year and selected_term:
        selected_term_obj = Term.objects.filter(
            school=school, academic_year_id=selected_year, term_number=selected_term
        ).first()

    # =====================================================
    # ACTIVE TERM
    # Used only when Year & Term are NOT selected
    # =====================================================

    active_term = Term.objects.filter(school=school, is_active=True).first()

    # =====================================================
    # WEEK NAVIGATION
    # =====================================================

    try:
        week_offset = int(request.GET.get("week", "0") or 0)
    except ValueError:
        week_offset = 0

    start_of_week = today_date - timedelta(days=today_date.weekday())
    start_of_week += timedelta(weeks=week_offset)

    # =====================================================
    # WEEK LIMITS
    # =====================================================

    can_go_previous = True
    can_go_next = True

    # Use selected term if filters are selected.
    # Otherwise use the active term.
    boundary_term = selected_term_obj or active_term

    if boundary_term:

        term_start_week = boundary_term.start_date - timedelta(
            days=boundary_term.start_date.weekday()
        )

        term_end_week = boundary_term.end_date - timedelta(
            days=boundary_term.end_date.weekday()
        )

        # =================================================
        # STOP BEFORE TERM START
        # =================================================

        if start_of_week <= term_start_week:
            start_of_week = term_start_week
            can_go_previous = False

        # =================================================
        # STOP AFTER TERM END
        # =================================================

        if start_of_week >= term_end_week:
            start_of_week = term_end_week
            can_go_next = False

    # =====================================================
    # YEARS
    # =====================================================

    years = AcademicYear.objects.filter(school=school).order_by("-name")

    # =====================================================
    # TIMETABLE
    # =====================================================

    timetable = Timetable.objects.filter(teacher=teacher, school=school).select_related(
        "subject", "school_class", "term", "academic_year"
    )

    # =====================================================
    # APPLY YEAR FILTER
    # =====================================================

    if selected_year:
        timetable = timetable.filter(academic_year_id=selected_year)

    # =====================================================
    # APPLY TERM FILTER
    # =====================================================

    if selected_term:
        timetable = timetable.filter(term__term_number=selected_term)

    timetable = timetable.order_by("weekday", "period")

    # =====================================================
    # DAYS
    # =====================================================

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    timetable_by_day = []

    for day_num in range(5):

        current_date = start_of_week + timedelta(days=day_num)

        # =================================================
        # SCHOOL EVENT
        # =================================================

        event = AcademicCalendar.objects.filter(
            school=school, start_date__lte=current_date, end_date__gte=current_date
        ).first()

        # =================================================
        # DAILY ENTRIES
        # =================================================

        entries = [e for e in timetable if e.weekday == day_num]

        # =================================================
        # EVENT AFFECTS TIMETABLE
        # =================================================

        if event and event.affects_timetable:
            entries = []

        timetable_by_day.append(
            {
                "num": day_num,
                "name": days[day_num],
                "date": current_date,
                "entries": entries,
                "event": event,
            }
        )

    # =====================================================
    # CONTEXT
    # =====================================================

    return render(
        request,
        "accounts/teacher_timetable.html",
        {
            "timetable_by_day": timetable_by_day,
            "today": today_date,
            "today_weekday": today_weekday if week_offset == 0 else -1,
            "week_offset": week_offset,
            "prev_week": week_offset - 1,
            "next_week": week_offset + 1,
            "current_week_start": start_of_week,
            "years": years,
            "selected_year": selected_year,
            "selected_term": selected_term,
            "selected_term_obj": selected_term_obj,
            "can_go_previous": can_go_previous,
            "can_go_next": can_go_next,
        },
    )


# ---------------------------
# TEACHER CLASSES
# --------------------------
@login_required
def teacher_classes(request):
    if request.user.role != "teacher":
        return redirect('accounts:dashboard')

    teacher = request.user
    school = teacher.school

    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')

    # ✅ Only classes from assignments in this school
    assignments = TeacherSubjectClass.objects.filter(
        teacher=teacher,
        school=school
    ).select_related('school_class')

    # Get unique classes
    class_ids = assignments.values_list('school_class_id', flat=True).distinct()
    classes = SchoolClass.objects.filter(
        id__in=class_ids,
        school=school
    ).order_by('name')

    # Count students per class (only from this school)
    classes_with_count = []
    for school_class in classes:
        student_count = User.objects.filter(
            role='student',
            school=school,
            school_class=school_class
        ).count()

        # Subjects teacher teaches in this class
        subjects_in_class = assignments.filter(
            school_class=school_class
        ).values_list('subject__name', flat=True)

        classes_with_count.append({
            'class': school_class,
            'student_count': student_count,
            'subjects': list(subjects_in_class)
        })

    return render(request, 'accounts/teacher_classes.html', {
        'classes_with_count': classes_with_count
    })

# -----------------------------------
# TEACHER UPLOAD RESULTS
# ----------------------------------


@login_required
def upload_result(request, class_id, subject_id):
    if request.user.role != "teacher":
        return redirect('accounts:dashboard')

    school = request.user.school  # ✅ from teacher, not from URL
    school_class = get_object_or_404(
        SchoolClass, id=class_id, school=school
    )  # ✅ isolated
    subject = get_object_or_404(Subject, id=subject_id, school=school)  
    term = Term.objects.filter(is_active=True, school=school).first()

    if not term:
        messages.error(
            request, "No active term set. Ask admin to activate a term first."
        )
        return redirect("accounts:teacher-dashboard")

    entered_results = (
        Result.objects.filter(
            school=school,  # ✅ isolation
            subject=subject,
            term=term,
            academic_year=term.academic_year,
            student__school_class=school_class,
        )
        .exclude(Q(exam_score__isnull=True) | Q(exam_score=0))
        .select_related("student")
    )

    if request.method == "POST":
        form = ResultUploadForm(request.POST, class_id=class_id, school=school)
        if form.is_valid():
            student = form.cleaned_data["student"]
            ca_data = request.POST.get("ca_data")

            ContinuousAssessment.objects.filter(
                student=student,
                subject=subject,
                term=term,
                academic_year=term.academic_year,
            ).delete()

            result = form.save(commit=False)

            ca_total = 0
            if ca_data:
                try:
                    assessments = json.loads(ca_data)
                    ca_total = sum(
                        float(i["score"])
                        for i in assessments
                        if str(i.get("score", "")).strip() != ""
                    )
                except:
                    ca_total = 0

            exam_str = (
                str(result.exam_score).strip() if result.exam_score is not None else ""
            )
            exam = float(exam_str) if exam_str != "" else 0

            if ca_total == 0 and exam == 0:
                messages.error(request, "Please enter CA or Exam score before saving.")
                return redirect(
                    "accounts:teacher-upload-results",
                    class_id=class_id,
                    subject_id=subject_id,
                )

            if (
                ca_total > term.sba_total
                or exam > 100
            ):
                messages.error(
                    request, "Score validation failed. SBA or Exam score exceeds its maximum and Exam cannot exceed 100."
                )
                return redirect(
                    "accounts:teacher-upload-results",
                    class_id=class_id,
                    subject_id=subject_id,
                )

            if Result.objects.filter(
                school=school,
                student=student,
                subject=subject,
                term=term,
                academic_year=term.academic_year,
            ).exists():
                messages.error(
                    request, f"Result already exists for {student.get_full_name()}."
                )
            else:
                result.subject = subject
                result.term = term
                result.academic_year = term.academic_year
                result.school = school  # ✅ isolation
                result.save()

                if ca_data:
                    try:
                        assessments = json.loads(ca_data)
                        for item in assessments:
                            if str(item.get("score", "")).strip() == "":
                                continue
                            ContinuousAssessment.objects.create(
                                student=student,
                                subject=subject,
                                term=term,
                                academic_year=term.academic_year,
                                title=item["title"],
                                score=item["score"],
                            )
                    except:
                        pass

                messages.success(request, f"Result saved for {student.get_full_name()}")
                return redirect(
                    "accounts:teacher-upload-results",
                    class_id=class_id,
                    subject_id=subject_id,
                )
    else:
        form = ResultUploadForm(class_id=class_id, school=school)

    return render(
        request,
        "accounts/upload_result.html",
        {
            "school_class": school_class,
            "subject": subject,
            "students": User.objects.filter(
                role="student", school_class_id=class_id, school=school
            ),  # ✅ isolation
            "active_term": term,
            "form": form,
            "entered_results": entered_results,
        },
    )


# --------------------------------
# UPLOAD RESULTS LIST NEW
# ------------------------------------
@login_required
def upload_results_list(request):
    assignments = TeacherSubjectClass.objects.filter(
        teacher=request.user,
        school=request.user.school,  # ✅ isolation
        school_class__school=request.user.school  # ✅ double safety
    ).select_related('school_class', 'subject')

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
    active_term = Term.objects.filter(is_active=True, school=school).first() or Term.objects.filter(school=school).first()

    if not active_term:
        messages.error(request, "No terms found.")
        return redirect('accounts:dashboard')

    selected_term_number = request.GET.get('term', str(active_term.term_number))
    selected_year_id = request.GET.get('year') or (active_term.academic_year.id if active_term.academic_year else None)
    try:
        selected_year_id = int(selected_year_id)
    except:
        selected_year_id = active_term.academic_year.id if active_term.academic_year else None

    term_obj = Term.objects.filter(school=school, term_number=selected_term_number, academic_year_id=selected_year_id).first() or active_term
    term_name = term_obj.get_term_number_display()
    academic_years = AcademicYear.objects.filter(term__school=school).distinct().order_by('-name')

    # ✅ FIX
    assignments = TeacherSubjectClass.objects.filter(
        teacher=request.user, 
        school=school,
        school_class__school=school
    ).select_related('school_class', 'subject')
    assigned_classes_count = assignments.values("school_class_id").distinct().count()

    classes_with_students = []
    for assignment in assignments:
        school_class = assignment.school_class
        subject = assignment.subject
        base_qs = Result.objects.filter(
            school=school,  # ✅
            subject=subject, term=term_obj, academic_year=term_obj.academic_year,
            student__school_class=school_class, status__in=['draft', 'returned', 'submitted']
        ).exclude(Q(exam_score__isnull=True) | Q(exam_score=0))

        if not base_qs.exists():
            continue

        students = User.objects.filter(role='student', school_class_id=school_class.id, school=school)  # ✅
        student_cards = []
        for student in students:
            results = Result.objects.filter(
                school=school,  # ✅
                student=student, subject=subject, term=term_obj,
                academic_year=term_obj.academic_year, status__in=['draft', 'returned', 'submitted']
            ).exclude(Q(exam_score__isnull=True) | Q(exam_score=0))

            if not results.exists():
                continue
            total = sum(r.total_score for r in results)
            count = results.count()
            average = total / count if count else 0
            grading = get_grading_scale(school, term_obj, average)

            grade = grading.grade if grading else "-"
            remark = grading.remark if grading else "-"
            student_cards.append({'student': student, 'results': results, 'total': total, 'average': average, 'grade': grade, 'remark': remark})

        if student_cards:
            classes_with_students.append({
                'school_class': school_class, 'subject': subject,
                'student_cards': student_cards,
                'draft_count': base_qs.filter(status__in=['draft', 'returned']).count()
            })

    return render(request, 'accounts/teacher_results.html', {
        'classes_with_students': classes_with_students,
        'assigned_classes_count': assigned_classes_count,
        'selected_term': term_obj.term_number,
        'selected_year': term_obj.academic_year.id,
        'term_obj': term_obj, 'term_name': term_name,
        'academic_years': academic_years, 
    })


@login_required
def teacher_published_results(request):
    from django.db.models import Q
    if request.user.role != 'teacher':
        return redirect('accounts:dashboard')

    school = request.user.school

    # ✅ FIX
    assignments = TeacherSubjectClass.objects.filter(
        teacher=request.user,
        school=school,
        school_class__school=school
    ).select_related('school_class', 'subject')

    subject_ids = [a.subject_id for a in assignments]
    class_ids = [a.school_class_id for a in assignments]

    results = Result.objects.filter(
        school=school,  # ✅
        status='published',
        subject_id__in=subject_ids,
        student__school=school,  # ✅
        student__school_class_id__in=class_ids,
    ).exclude(Q(exam_score__isnull=True) | Q(exam_score=0)).select_related('student', 'subject', 'student__school_class').order_by('-published_at')

    class_id = request.GET.get('class')
    term = request.GET.get('term')
    year = request.GET.get('year')

    if class_id:
        results = results.filter(student__school_class_id=class_id)
    if term:
        results = results.filter(term__term_number=term)
    if year:
        results = results.filter(academic_year_id=year)

    classes = SchoolClass.objects.filter(id__in=class_ids, school=school).distinct()  # ✅
    years = AcademicYear.objects.filter(term__school=school).distinct().order_by('-name')  # ✅

    setting = SchoolSetting.objects.filter(school=school).first()
    attendance_mode = setting.attendance_mode if setting else 'subject'

    return render(request, 'accounts/teacher_published_results.html', {
        'results': results, 'classes': classes, 'years': years,
        'attendance_mode': attendance_mode,
    })


def student_results_table_view(request):
    school = request.user.school

    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')

    # ✅ Level 1: School isolation
    summaries = StudentTermSummary.objects.select_related(
        'student', 'school_class'
    ).filter(
        school=school,  # ✅
        student__role='student',
        student__school=school,  # ✅
        school_class__school=school  # ✅
    ).order_by('school_class__name', 'rank', 'student__last_name')

    students = []
    for summary in summaries:
        student_results = Result.objects.filter(
            school=school,  # ✅ Level 1
            student=summary.student,
            student__school=school
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
            'results': student_results
        })

    return render(request, 'accounts/student_results_table.html', {'students': students})


# -----------------------------------
# TEACHER EDIT RESULTS
# ----------------------------------
@login_required
def edit_result(request, pk):
    if request.user.role != "teacher":
        return redirect("accounts:dashboard")
    school = request.user.school
    teacher = request.user

    assignments = TeacherSubjectClass.objects.filter(
        teacher=teacher,
        school=school
    )

    subject_ids = assignments.values_list("subject_id", flat=True)
    class_ids = assignments.values_list("school_class_id", flat=True)

    result = get_object_or_404(
        Result,
        pk=pk,
        school=school,
        subject_id__in=subject_ids,
        student__school_class_id__in=class_ids,
        student__school=school,
    )

    # Prevent editing submitted/published results
    if result.status not in ["draft", "returned"]:
        messages.error(
            request,
            "You can't edit this result. It was already submitted to admin."
        )
        return redirect("accounts:teacher-results")

    ca_records = ContinuousAssessment.objects.filter(
        school=school,
        student=result.student,
        subject=result.subject,
        term=result.term,
        academic_year=result.academic_year,
    ).order_by("created_at")

    if request.method == "POST":

        form = ResultForm(request.POST, instance=result)

        if form.is_valid():

            # ---------------------------------------------
            # GET NEW CA SCORES
            # ---------------------------------------------

            ca_total = 0

            for ca in ca_records:

                score_value = request.POST.get(
                    f"ca_{ca.id}",
                    str(ca.score)
                )

                try:
                    score_value = float(score_value)
                except (ValueError, TypeError):
                    score_value = 0

                if score_value < 0:
                    score_value = 0

                ca_total += score_value

            # ---------------------------------------------
            # GET NEW EXAM SCORE
            # ---------------------------------------------

            exam_score = form.cleaned_data.get("exam_score")

            exam = float(exam_score or 0)

            # ---------------------------------------------
            # VALIDATION
            # RAW SBA -> CONVERTED CA
            # ---------------------------------------------

            if ca_total > float(result.term.sba_total or 0):

                messages.error(
                    request,
                    f"SBA score cannot exceed the school's "
                    f"maximum SBA score of {result.term.sba_total}."
                )

                return redirect(
                    "accounts:edit-result",
                    pk=result.id
                )

            # Convert raw SBA to final CA
            converted_ca = 0

            if result.term.sba_total:
                converted_ca = round(
                    (ca_total / float(result.term.sba_total))
                    * float(result.term.ca_total),
                    2
                )

            if exam > 100:

                messages.error(
                    request,
                    "Exam score cannot exceed 100."
                    
                )

                return redirect(
                    "accounts:edit-result",
                    pk=result.id
                )

            converted_exam = 0

            if result.term.exam_total:
                converted_exam = round(
                    (exam / 100) * float(result.term.exam_total),
                    2
                )

            if converted_ca + converted_exam > 100:

                messages.error(
                    request,
                    "The CA and Exam scores together cannot exceed 100."
                )

                return redirect(
                    "accounts:edit-result",
                    pk=result.id
                )

     

            # ---------------------------------------------
            # SAVE RESULT
            # ---------------------------------------------

            updated_result = form.save(commit=False)

            updated_result.student = result.student
            updated_result.school = school
            updated_result.subject = result.subject
            updated_result.term = result.term
            updated_result.academic_year = result.academic_year

            updated_result.save()

            # ---------------------------------------------
            # SAVE CA SCORES
            # ---------------------------------------------

            for ca in ca_records:

                score_value = request.POST.get(
                    f"ca_{ca.id}"
                )

                if score_value is not None:

                    try:
                        score_value = float(score_value)

                        if 0 <= score_value <= 100:
                            ca.score = score_value
                            ca.save(update_fields=["score"])

                    except (ValueError, TypeError):
                        pass

            messages.success(
                request,
                "Result updated successfully. Don't forget to submit it to the administrator again."
            )

            return redirect("accounts:teacher-results")

    else:

        form = ResultForm(instance=result)

    return render(
        request,
        "accounts/edit_result.html",
        {
            "form": form,
            "result": result,
            "ca_records": ca_records,
        },
    )

@login_required
def submit_results_to_admin(request):
    if request.user.role != 'teacher':
        return redirect('accounts:dashboard')
    
    if request.method != 'POST':
        messages.error(request, "Use the button to submit.")
        return redirect('accounts:teacher-results')
    
    school = request.user.school  # ✅ Level 1
    class_id = request.POST.get('class_id')
    subject_id = request.POST.get('subject_id')
    term_id = request.POST.get('term')
    year_id = request.POST.get('year')

    term_id = int(term_id)
    year_id = int(year_id)
    
    if not all([class_id, subject_id, term_id, year_id]):
        messages.error(request, "Missing required information.")
        return redirect('accounts:teacher-results')
    
    # ✅ Security check - BOTH levels
    if not TeacherSubjectClass.objects.filter(
        teacher=request.user, 
        school=school,  # ✅ Level 1
        subject_id=subject_id,  # ✅ Level 2
        school_class_id=class_id,  # ✅ Level 2
        school_class__school=school
    ).exists():
        messages.error(request, "You are not assigned to this subject and class.")
        return redirect('accounts:teacher-results')
    
    drafts = Result.objects.filter(
        school=school,  # ✅ Level 1
        subject_id=subject_id,  # ✅ Level 2
        student__school=school,  # ✅ Level 1
        student__school_class_id=class_id,  # ✅ Level 2
        student__school_class__school=school,
        term__term_number=term_id,
        term__school=school,
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

    # ✅ Level 1 fix
    setting = SchoolSetting.objects.filter(school=school).first() # ✅
    attendance_mode = setting.attendance_mode if setting else "subject"
    
    if attendance_mode == "class_teacher":
        if not getattr(teacher, 'is_class_teacher', False):
            messages.error(request, "Only class teachers can mark attendance.")
            return redirect('accounts:dashboard')

    # ✅ Level 1 + Level 2
    tsc_options = TeacherSubjectClass.objects.filter(
        teacher=teacher,
        school=school, # ✅ Level 1
        school_class__school=school
    ).select_related('subject', 'school_class')

    if attendance_mode == "class_teacher":
        tsc_options = TeacherSubjectClass.objects.filter(
            school=school, # ✅
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
    students = []
    tsc_obj = None
    session = None
    attendance_dict = {}
    min_date = (date.today() - timedelta(days=30)).isoformat()
    max_date = date.today().isoformat()
    
    if selected_tsc_id:
        if attendance_mode == "class_teacher":
            tsc_obj = get_object_or_404(TeacherSubjectClass, id=selected_tsc_id, school=school, school_class__class_teacher=teacher, school_class__school=school)
        else:
            tsc_obj = get_object_or_404(TeacherSubjectClass, id=selected_tsc_id, teacher=teacher, school=school, school_class__school=school)
    
    if tsc_obj:
        students = User.objects.filter(role='student', school_class_id=tsc_obj.school_class_id, school=school).order_by('last_name', 'first_name')
        
    if request.method == 'POST' and tsc_obj:
        selected_date_obj = datetime.strptime(request.GET.get('date', date.today().isoformat()), '%Y-%m-%d').date()
        if selected_date_obj.weekday() >= 5:
            messages.error(request, "Cannot mark attendance on weekends.")
            return redirect('accounts:mark_attendance')
        
        is_holiday = AcademicCalendar.objects.filter(school=school, affects_timetable=True, start_date__lte=selected_date_obj, end_date__gte=selected_date_obj).exists()
        if is_holiday:
            messages.error(request, f"Cannot save attendance. {selected_date_obj} is a holiday.")
            return redirect('accounts:mark_attendance')
        
        subject_id = TeacherSubjectClass.objects.filter(school_class_id=tsc_obj.school_class_id).first().subject_id if attendance_mode == "class_teacher" else tsc_obj.subject_id

        # ✅ Level 1 fix
        session, created = AttendanceSession.objects.get_or_create(
            school=school,  # ✅
            school_class_id=tsc_obj.school_class_id,
            subject_id=subject_id,
            date=request.GET.get('date', date.today().isoformat()),
            teacher=teacher
        )
        
        marked_count = 0
        with transaction.atomic():
            for student in students:
                status = request.POST.get(f'status_{student.id}')
                if status not in ['P', 'A']:
                    status = None
                if status:
                    AttendanceRecord.objects.update_or_create(session=session, student=student, defaults={'status': status})
                    marked_count += 1
                else:
                    AttendanceRecord.objects.filter(session=session, student=student).delete()
            
            marked_ids = [s.id for s in students if request.POST.get(f'status_{s.id}') in ['P', 'A']]
            unmarked_students = students.exclude(id__in=marked_ids)
            for student in unmarked_students:
                AttendanceRecord.objects.update_or_create(session=session, student=student, defaults={'status': 'A'})
            
        messages.success(request, f"Attendance for {tsc_obj.school_class.name} saved.")
        return redirect('accounts:attendance_detail', session_id=session.id)
    
    if tsc_obj:
        session = AttendanceSession.objects.filter(school=school, school_class_id=tsc_obj.school_class_id, subject=tsc_obj.subject, date=request.GET.get('date', date.today().isoformat()), teacher=teacher).first()
        if session:
            existing = AttendanceRecord.objects.filter(session=session)
            attendance_dict = {a.student_id: a.status for a in existing}
    
    return render(request, 'accounts/mark_attendance.html', {
        'tsc_options': tsc_options, 'students': students, 'selected_tsc': tsc_obj,
        'selected_tsc_id': selected_tsc_id, 'selected_date': request.GET.get('date', date.today().isoformat()),
        'attendance_dict': attendance_dict, 'session': session, 
        'min_date': min_date, 'max_date': max_date, 'today': date.today(),
        'attendance_mode': attendance_mode,
    })
# --------------------------------

# --------------------------------
@login_required
def attendance_mark(request, session_id):
    school = request.user.school
    teacher = request.user

    # ✅ Level 1 + Level 2: Only session from this school AND this teacher's assignment
    session = get_object_or_404(
        AttendanceSession.objects.select_related('school_class', 'subject'),
        id=session_id,
        school=school,  # ✅ Level 1
        school_class__school=school,
        teacher=teacher  # ✅ Level 2 - only his own sessions
    )

    students = User.objects.filter(
        school_class=session.school_class,
        school=school,  # ✅ Level 1
        role='student',
        is_active=True
    ).order_by('first_name')

    if not students.exists():
        messages.error(request, f"No students found in {session.school_class.name}.")
        return redirect('accounts:attendance_create')

    if request.method == 'POST':
        for student in students:
            status = request.POST.get(f'status_{student.id}', '')
            if status in ['P', 'A']: # Only allow P/A
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
    # --- FIXED PERMISSION ---
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress',
        'teacher',
    ]:
        return redirect('accounts:dashboard')

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

        teacher_classes = (
            SchoolClass.objects.filter(
                teachersubjectclass__teacher=teacher,
                teachersubjectclass__school=school,
                school=school,
            )
            .distinct()
            .order_by("name")
        )

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
                    school_class__school=school, #
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
    if request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
    "teacher",
]:
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school:
        messages.error(request, "No school assigned")
        return redirect("accounts:dashboard")

    setting = SchoolSetting.objects.filter(school=school).first()
    attendance_mode = setting.attendance_mode if setting else "subject"

    user = request.user

    is_school_admin = getattr(user, "role", "") in [
        "admin",
        "proprietor",
        "proprietress",
        "headmaster",
        "headmistress",
    ]

    # =====================================================
    # GET BASE ATTENDANCE SESSION
    # =====================================================

    if user.is_staff or user.is_superuser:

        base_session = get_object_or_404(
            AttendanceSession, id=session_id, school=school
        )

    elif is_school_admin:

        base_session = get_object_or_404(
            AttendanceSession, id=session_id, school=school, school__id=school.id
        )

    else:

        if attendance_mode == "class_teacher":

            base_session = get_object_or_404(
                AttendanceSession,
                id=session_id,
                school=school,
                school_class__school=school,
                school_class__class_teacher=user,
            )

        else:

            base_session = get_object_or_404(
                AttendanceSession,
                id=session_id,
                teacher=user,
                school=school,
                school_class__school=school,
            )

    # =====================================================
    # SCHOOL WEEK
    # =====================================================
    #
    # IMPORTANT:
    # This is NOT an ISO calendar week.
    #
    # Week 1 begins on the actual Term.start_date
    # configured by the school administrator.
    #
    # The first week can therefore be a partial week.
    #
    # Example:
    #
    # Term starts Wednesday:
    #
    # Wed - Fri  = Week 1
    # Mon - Fri  = Week 2
    # Mon - Fri  = Week 3
    #
    # =====================================================

    session_date = base_session.date

    attendance_term = (
        Term.objects.filter(
            school=school, start_date__lte=session_date, end_date__gte=session_date
        )
        .select_related("academic_year")
        .first()
    )

    attendance_week = None

    if attendance_term:

        term_start = attendance_term.start_date

        # Monday of the calendar week containing the
        # actual term start date.
        first_monday = term_start - timedelta(days=term_start.weekday())

        # Friday of that first school week.
        first_week_friday = first_monday + timedelta(days=4)

        # -------------------------------------------------
        # WEEK 1
        # -------------------------------------------------

        if session_date <= first_week_friday:

            attendance_week = 1

        # -------------------------------------------------
        # WEEK 2+
        # -------------------------------------------------

        else:

            first_full_monday = first_monday + timedelta(days=7)

            attendance_week = ((session_date - first_full_monday).days // 7) + 2

    # =====================================================
    # CLASS TEACHER MODE
    # =====================================================

    if attendance_mode == "class_teacher":

        all_sessions = (
            AttendanceSession.objects.filter(
                date=base_session.date,
                school_class=base_session.school_class,
                school_class__school=school,
                school=school,
            )
            .annotate(total=Count("records"))
            .filter(total__gt=0)
        )

        all_records = AttendanceRecord.objects.filter(
            session__in=all_sessions, session__school=school
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

        present_count = sum(1 for statuses in by_student.values() if "P" in statuses)

        absent_count = sum(1 for statuses in by_student.values() if "P" not in statuses)

        total_count = len(by_student)

        attendance_rate = (present_count / total_count * 100) if total_count else 0

        paginator = Paginator(merged_list, 25)

        page_obj = paginator.get_page(request.GET.get("page"))

    # =====================================================
    # SUBJECT ATTENDANCE MODE
    # =====================================================

    else:

        records = (
            AttendanceRecord.objects.filter(
                session=base_session, session__school=school
            )
            .select_related("student")
            .order_by("student__last_name")
        )

        total_count = records.count()

        present_count = records.filter(status="P").count()

        absent_count = records.filter(status="A").count()

        attendance_rate = (present_count / total_count * 100) if total_count else 0

        paginator = Paginator(records, 25)

        page_obj = paginator.get_page(request.GET.get("page"))

    # =====================================================
    # TEACHER SUBJECT CLASS
    # =====================================================

    tsc_obj = TeacherSubjectClass.objects.filter(
        school_class=base_session.school_class,
        school=school,
        school_class__school=school,
    ).first()

    # =====================================================
    # RENDER
    # =====================================================

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
            # SCHOOL WEEK
            "attendance_week": attendance_week,
            # USED BY TEMPLATE TO CONTROL
            # WHO SEES "MARKED BY"
            "is_school_admin": is_school_admin,
        },
    )


@login_required
def attendance_create_session(request):
    if request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
    "teacher",
]:
        return redirect("accounts:dashboard")
    school = request.user.school
    
    # Get teacher assignments for Level 2
    teacher_assignments = TeacherSubjectClass.objects.filter(
        teacher=request.user,
        school=school
    )
    allowed_class_ids = teacher_assignments.values_list('school_class_id', flat=True)
    allowed_subject_ids = teacher_assignments.values_list('subject_id', flat=True)

    if request.method == 'POST':
        school_class_id = request.POST.get('school_class')
        subject_id = request.POST.get('subject')
        session_date = request.POST.get('date')
        
        # ✅ FIX 1 & 2 - check school + assignment
        school_class = get_object_or_404(
            SchoolClass, 
            id=school_class_id, 
            school=school,
            id__in=allowed_class_ids
        )
        subject = get_object_or_404(
            Subject, 
            id=subject_id, 
            school=school,
            id__in=allowed_subject_ids
        )
        
        session, created = AttendanceSession.objects.get_or_create(
            date=session_date,
            school_class=school_class,
            school_class__school=school,
            subject=subject,
            subject__school=school,
            teacher=request.user,
            school=school
        )
        return redirect('accounts:attendance_mark', session_id=session.id)
    
    # ✅ FIX - only show his school + his assigned classes/subjects
    classes = SchoolClass.objects.filter(
        school=school,
        id__in=allowed_class_ids
    ).order_by('name')
    
    subjects = Subject.objects.filter(
        school=school,
        id__in=allowed_subject_ids
    ).order_by('name')

    return render(request, 'accounts/attendance_create.html', {
        'classes': classes,
        'subjects': subjects,
        'today': dt_date.today().strftime('%Y-%m-%d')
    })


# TEACHER STUDENTS LIST
# --------------------------
@login_required
def teacher_student_list(request):
    if request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
    "teacher",
]:
        return redirect('accounts:dashboard')
    return render(request, 'accounts/teacher_student_list.html')

# ---------------------------
# TEACHER ATTENDANCE
# --------------------------
@login_required
def teacher_attendance_report(request):
    if request.user.role not in ['teacher','admin', 'headmaster', 'headmistress', 'proprietor', 'proprietress']:
        return redirect('accounts:dashboard')
    return render(request, 'accounts/teacher_attendance_report.html')

# ---------------------------
# TEACHER ASSIGNMENTS
# --------------------------
@login_required
def teacher_assignments(request):
    teacher = request.user
    school = teacher.school

    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')

    assignments = TeacherSubjectClass.objects.filter(
        teacher=teacher,
        school=school  # ✅ Now permanent multi-school
    ).select_related('subject', 'school_class', 'school').order_by('school_class__name', 'subject__name')

    return render(request, 'accounts/teacher_assignments.html', {
        'assignments': assignments
    })


# -------------------------------


# ----------------------------
@login_required
def school_settings(request):
    if request.user.role in ["admin", "proprietor", "proprietress"]:
        pass
    elif request.user.role in ["headmaster", "headmistress"]:
        try:
            owner_exists = User.objects.filter(
                school=request.user.school,
                role__in=["admin", "proprietor", "proprietress"]
            ).exists()

            # Headmaster-only school:
            # Headmaster/Headmistress has full management access.
            if not owner_exists:
                pass

            # Owner-controlled school:
            # School Settings depends on the permission granted by the owner.
            else:
                hp = request.user.school.headmaster_permissions
                if not hp.can_school_settings:
                    messages.error(
                        request,
                        "No permission - Ask admin to enable School Settings"
                    )
                    return redirect("accounts:headmaster_dashboard")

        except HeadmasterPermission.DoesNotExist:
            messages.error(
                request,
                "No permissions set yet - Ask admin to set permissions"
            )
            return redirect("accounts:headmaster_dashboard")
        except Exception as e:
            messages.error(request, f"Error: {e}")
            return redirect("accounts:headmaster_dashboard")
    else:
        messages.error(request, "Only school owners can access this page.")
        return redirect("accounts:dashboard")
    school = request.user.school
    if not school:
        messages.error(request, "Your school not found.")
        return redirect("accounts:dashboard")

    setting, _ = SchoolSetting.objects.get_or_create(
        school=school, defaults={"attendance_mode": "subject"}
    )

    # === 1. UPDATE SCHOOL PROFILE ===
    if request.method == "POST" and "update_school" in request.POST:
        school.name = request.POST.get("name") or school.name
        school.address = request.POST.get("address") or school.address
        school.phone = request.POST.get("phone") or school.phone
        school.email = request.POST.get("email") or school.email
        school.gps_address = request.POST.get("gps_address") or school.gps_address
        school.motto = request.POST.get("motto") or school.motto
        logo = request.FILES.get("logo")
        if logo:
            school.logo = logo
        if request.POST.get("attendance_mode"):
            setting.attendance_mode = request.POST.get("attendance_mode")
            setting.save()
        school.save()
        messages.success(request, "School profile saved.")
        return redirect("accounts:school_settings")

    if request.method == "POST" and "update_attendance" in request.POST:
        mode = request.POST.get("attendance_mode") or "subject"
        setting.attendance_mode = mode
        setting.save()
        messages.success(request, f"Attendance mode changed to {mode}")
        return redirect("accounts:school_settings")

    if request.method == "POST" and "update_permission" in request.POST:
        school.can_teachers_manage_students = "can_teachers_manage_students" in request.POST
        school.save()
        messages.success(request, "Permission updated")
        return redirect("accounts:school_settings")

    if request.method == "POST" and "update_admission_letter" in request.POST:
        school.admission_letter_title = request.POST.get("admission_letter_title") or school.admission_letter_title
        school.admission_letter_body = request.POST.get("admission_letter_body") or school.admission_letter_body
        school.admission_letter_footer = request.POST.get("admission_letter_footer") or school.admission_letter_footer
        school.save()
        messages.success(request, "Admission letter template saved.")
        return redirect("accounts:school_settings")

    # === DYNAMIC FEE PART - NEW ===
    selected_year = request.GET.get("year")
    selected_term_raw = request.GET.get("term")
    selected_term = None
    if selected_term_raw and selected_term_raw not in ["None", "null", "all", ""]:
        try:
            selected_term = int(selected_term_raw)
        except:
            selected_term = None
    if not selected_year or selected_year in ["None", "null", "all", ""]:
        selected_year = None

    academic_years = AcademicYear.objects.filter(school=school).order_by("-name")
    terms = Term.objects.filter(school=school).order_by("term_number")
    active_term = Term.objects.filter(school=school, is_active=True).first()
    payment_types = PaymentType.objects.filter(school=school).order_by('name')

    # Use DYNAMIC not OLD FeeStructure
    fee_qs = DynamicFeeStructure.objects.filter(school=school)
    if selected_year:
        fee_qs = fee_qs.filter(academic_year=selected_year)
    if selected_term:
        fee_qs = fee_qs.filter(term_id=selected_term)

    # Auto-select year if none
    if not selected_year:
        first_fee = fee_qs.first()
        if first_fee:
            selected_year = first_fee.academic_year
        elif active_term:
            selected_year = str(active_term.academic_year)
        elif academic_years.first():
            selected_year = academic_years.first().name

    # === CREATE DYNAMIC FEE - FIXED FOR fee_name[] ARRAY ===
    if request.method == "POST" and "create_fee_structure" in request.POST:
        term_id = request.POST.get("term")
        term = get_object_or_404(Term, id=term_id, school=school)
        academic_year = request.POST.get("academic_year")
        stage = request.POST.get("stage")
        due_date = request.POST.get("due_date") or None

        # Get arrays from form - THIS IS WHAT YOUR TEMPLATE SENDS
        fee_names = request.POST.getlist("fee_name[]")
        fee_amounts = request.POST.getlist("fee_amount[]")

        # Create or update structure
        dyn_fee, created = DynamicFeeStructure.objects.get_or_create(
            school=school,
            stage=stage,
            term=term,
            academic_year=academic_year,
            defaults={'due_date': due_date}
        )
        if not created:
            dyn_fee.due_date = due_date
            dyn_fee.save()
            dyn_fee.items.all().delete()  # clear old to replace

        # Loop through the arrays
        for name, amount in zip(fee_names, fee_amounts):
            if name.strip() and amount:
                try:
                    amt = Decimal(amount)
                except:
                    amt = Decimal('0')

                # Try to find PaymentType by name, or create if not exists
                pt = PaymentType.objects.filter(school=school, name__iexact=name.strip()).first()
                if not pt:
                    pt = PaymentType.objects.create(
                        school=school,
                        name=name.strip(),
                        code=name.strip().lower().replace(' ', '_')
                    )

                DynamicFeeStructureItem.objects.create(
                    school=school,
                    fee_structure=dyn_fee,
                    name=name.strip(),
                    amount=amt,
                    payment_type=pt
                )

        messages.success(request, f"Fee structure {'created' if created else 'updated'} for {stage} - {term} - {academic_year}. Total: ₵{dyn_fee.total_amount}")
        # IMPORTANT: pass fee_breakdown to show summary like in screenshot 2
        return redirect(f"{request.path}?year={academic_year}&term={term.id}&breakdown={dyn_fee.id}")

    # === FOR FEE BREAKDOWN SUMMARY (to show cards like screenshot 2) ===
    fee_breakdown = None
    breakdown_id = request.GET.get("breakdown")
    if breakdown_id:
        fee_breakdown = DynamicFeeStructure.objects.filter(id=breakdown_id, school=school).first()
    else:
        # If no breakdown param, show last created
        fee_breakdown = fee_qs.order_by('-created_at').first()

    return render(
            request,
            "accounts/school_settings.html",
            {
                "school": school,
                "fee_structures": fee_qs.order_by('-created_at'),
                "fee_structures_count": fee_qs.count(),
                "years": academic_years,
                "selected_year": selected_year,
                "selected_term": selected_term,
                "active_term": active_term,
                "terms": terms,
                "setting": setting,
                "payment_types": payment_types,
                "fee_breakdown": fee_breakdown,  
                "stage_choices": [  
                    ('creche','Creche'),
                    ('nursery','Nursery'),
                    ('kg','KG'),
                    ('lower_primary','Lower Primary'),
                    ('upper_primary','Upper Primary'),
                    ('jhs','JHS'),
                    ('shs','SHS'),
                ],
            },
        )


@login_required
def assign_students_to_class(request):
    if request.user.role not in ["teacher", "admin", "proprietor", "proprietress", "headmistress", "headmaster"]:
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school.can_teachers_manage_students and request.user.role == "teacher":
        messages.error(
            request,
            "Your school does not allow teachers to assign students to classes.",
        )
        return redirect("accounts:dashboard")

    tsc_list = TeacherSubjectClass.objects.filter(
        teacher=request.user, school=school, school_class__school=school
    )

    selected_tsc = None
    assigned_ids = []
    students = User.objects.filter(
        role="student", school=school, is_active=True
    ).order_by("first_name", "last_name")

    tsc_id = request.GET.get("tsc")
    if tsc_id:
        selected_tsc = get_object_or_404(
            TeacherSubjectClass, id=tsc_id, teacher=request.user, school=school
        )

        if request.method == "POST":
            checked_ids = request.POST.getlist("student_ids")

            # ✅ FIXED: filter by school
            StudentSubjectClass.objects.filter(
                school_class=selected_tsc.school_class,
                subject=selected_tsc.subject,
                school=school,
                school_class__school=school,
            ).delete()

            for sid in checked_ids:
                # ensure student belongs to same school
                if not students.filter(id=sid).exists():
                    continue
                StudentSubjectClass.objects.create(
                    student_id=sid,
                    school_class=selected_tsc.school_class,
                    subject=selected_tsc.subject,
                    school=school,  # ✅ add school field if your model has it
                )
            messages.success(request, "Student list updated successfully.")
            return redirect(f"{request.path}?tsc={tsc_id}")

        assigned_ids = StudentSubjectClass.objects.filter(
            school_class=selected_tsc.school_class,
            subject=selected_tsc.subject,
            school=school,
        ).values_list("student_id", flat=True)

    return render(
        request,
        "accounts/assign_students_to_class.html",
        {
            "tsc_list": tsc_list,
            "selected_tsc": selected_tsc,
            "students": students,
            "assigned_ids": list(assigned_ids),
        },
    )


@login_required
def view_students_by_class(request):
    if request.user.role not in ["teacher", "admin","proprietor", "proprietress", "headmistress", "headmaster"]:
        return redirect("accounts:dashboard")

    school = request.user.school

    # === TEACHER: only his assigned classes ===
    if request.user.role == "teacher":
        my_class_ids = TeacherSubjectClass.objects.filter(
            teacher=request.user,
            school=school,
            school_class__school=school
        ).values_list('school_class_id', flat=True).distinct()

        classes = SchoolClass.objects.filter(
            school=school,
            id__in=my_class_ids
        ).annotate(
            student_count=Count('students', filter=Q(students__role='student'))
        ).order_by('name')
    else:
        # Admin / Headmaster sees all
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
        grouped[stage].setdefault(base_class, {"main": None, "sections": []})
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
            has_real_sections = any(
                section.name.strip()!= base_class.strip()
                for section in data["sections"]
            )
            if has_real_sections:
                total_classes += len(data["sections"])
            else:
                total_classes += 1

    if request.user.role == "teacher":
        total_students = User.objects.filter(
            school=school, role='student',
            school_class_id__in=my_class_ids
        ).count()
    else:
        total_students = User.objects.filter(school=school, role='student').count()

    return render(request, 'accounts/view_students_by_class.html', {
        'grouped': grouped,
        'total_classes': total_classes,
        'total_students': total_students,
    })


@login_required  
def view_students_in_class(request, class_id):
    if request.user.role not in ["teacher", "admin", "headmaster", "headmistress", "proprietor", "proprietress"]:
        return redirect("accounts:dashboard")

    school = request.user.school
    credentials = request.session.get("credentials", None)

    school_class = get_object_or_404(SchoolClass, id=class_id, school=school)
    
    # === TEACHER CHECK: is this his assigned class? ===
    if request.user.role == "teacher":
        is_my_class = TeacherSubjectClass.objects.filter(
            teacher=request.user,
            school=school,
            school_class=school_class
        ).exists()
        if not is_my_class:
            messages.error(request, "You are not assigned to this class.")
            return redirect("accounts:view-students-by-class")
    
    students_in_class = User.objects.filter(
        school=school,
        school_class=school_class,  # use school_class field
        role='student', 
        is_active=True
    )
    
    if request.method == 'POST' and request.POST.get('add_to_all'):
        # Only admin/headmaster can add subjects to all
        if request.user.role == "teacher":
            messages.error(request, "No permission.")
            return redirect("accounts:view-students-in-class", class_id=school_class.id)
            
        subject_ids = request.POST.getlist('subject_ids')
        for student in students_in_class:
            for subject_id in subject_ids:
                StudentSubjectClass.objects.get_or_create(
                    student=student,
                    subject_id=subject_id,
                    school_class=school_class,
                    school=school
                )
        messages.success(request, 'Subjects added to all students!')
        return redirect('accounts:view-students-in-class', class_id=school_class.id)
    
    if request.method == 'POST' and request.POST.get('student_id'):
        if request.user.role == "teacher":
            messages.error(request, "No permission.")
            return redirect("accounts:view-students-in-class", class_id=school_class.id)

        student_id = request.POST.get('student_id')
        subject_ids = request.POST.getlist('subject_ids')
        student = get_object_or_404(User, id=student_id, role='student', school=school, is_active=True)
        
        for subject_id in subject_ids:
            StudentSubjectClass.objects.get_or_create(
                student=student,
                subject_id=subject_id,
                school_class=school_class,
                school=school
            )
        messages.success(request, f'Subjects added to {student.first_name}!')
        return redirect('accounts:view-students-in-class', class_id=school_class.id)
    
    students_list = students_in_class.order_by('last_name', 'first_name')
    paginator = Paginator(students_list, 4) 
    page_number = request.GET.get('page')
    students = paginator.get_page(page_number)
    
    all_subjects = Subject.objects.filter(school=school)

    return render(request, 'accounts/manage_students.html', {  
        'students': students,
        'school_class': school_class,
        'all_subjects': all_subjects,  
        'credentials': credentials
    })


@login_required
def deactivated_students(request, class_id):
    if request.user.role not in ["teacher", "admin", "headmaster", "headmistress", "propreitor", "proprietress"]:
        messages.error(request, "Only admin/headmaster can view deactivated students.")
        return redirect("accounts:dashboard")

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

    if request.user.role not in ["teacher", "admin", "headmaster"]:
        messages.error(request, "Only admin/headmaster can reactivate students.")
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
    school = student.school
    
    subjects = StudentSubjectClass.objects.filter(
        student=student,
        student__school=school,
        school_class__school=school
    ).select_related('subject', 'school_class').order_by('subject__name')
    
    context = {
        'subjects': subjects,
        'student': student,
    }
    return render(request, 'accounts/view_student_subjects.html', context)


@login_required
def student_subject_progress(request, subject_id):
    if request.user.role != 'student':
        return redirect('accounts:dashboard')
    
    student = request.user
    school = student.school
    
    # ISOLATED: Only subject from student's school
    subject = get_object_or_404(Subject, id=subject_id, school=school)
    
    results = Result.objects.filter(
        student=student,
        student__school=school,  # isolation
        subject=subject,
        subject__school=school,  # isolation
        status='published'  # you had submitted, but parent view uses published - use published
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

    results = (
        Result.objects.filter(
            student=student,
            student__school=student.school,
            term__term_number=selected_term.term_number,
            academic_year_id=selected_term.academic_year.id,
            status="published",
        )
        .select_related("subject", "term")
        .order_by("subject__code")
    )
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

    grading = GradingScale.objects.filter(
        school=student.school,
        term=selected_term,
        min_score__lte=percentage,
        max_score__gte=percentage,
    ).first()

    if not grading:
        grading = GradingScale.objects.filter(
            school=student.school,
            term__isnull=True,
            min_score__lte=percentage,
            max_score__gte=percentage,
        ).first()

    overall_grade = grading.grade if grading else "-"
    overall_remark = grading.remark if grading else ""
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

    if not overall_remark and overall_grade != "-":
        grading = GradingScale.objects.filter(
            school=student.school,
            term=selected_term,
            grade=overall_grade,
        ).first()

        if not grading:
            grading = GradingScale.objects.filter(
                school=student.school,
                term__isnull=True,
                grade=overall_grade,
            ).first()

        overall_remark = grading.remark if grading else ""

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
    student = getattr(request.user, "student", None) or request.user
    school = getattr(student, "school", None)
    if not school:
        return redirect("accounts:dashboard")

    years = AcademicYear.objects.filter(school=school).order_by("-name")
    terms = (
        Term.objects.filter(school=school)
        .select_related("academic_year")
        .order_by("-start_date")
    )

    year_id = request.GET.get("year")
    term_number = request.GET.get("term")

    # STUDENT FEE - SCHOOL ISOLATED
    fees_qs = (
        StudentFee.objects.filter(student=student, school=school)
        .select_related("term")
        .prefetch_related("dynamic_items")
        .order_by("-academic_year")
    )

    if year_id:
        try:
            year_obj = AcademicYear.objects.get(id=year_id, school=school)
            fees_qs = fees_qs.filter(academic_year=year_obj.name)
        except:
            pass
    if term_number:
        fees_qs = fees_qs.filter(term__term_number=term_number)

    latest_fee = fees_qs.first()

    # TRANSACTIONS - SCHOOL ISOLATED
    transactions = PaymentTransaction.objects.filter(
        student=student, school=school, is_voided=False
    ).select_related("term", "academic_year")

    if year_id:
        transactions = transactions.filter(academic_year_id=year_id)
    if term_number:
        transactions = transactions.filter(term__term_number=term_number)

    real_total_paid = transactions.aggregate(total=Sum("total_amount"))["total"] or 0

    real_balance = 0
    if latest_fee:
        # DYNAMIC ITEMS - THIS IS THE FIX
        dynamic_items = latest_fee.dynamic_items.filter(school=school).order_by("name")

        latest_fee.breakdown_items = []
        latest_fee.payment_options = []

        total_due = 0
        total_paid_dynamic = 0

        for item in dynamic_items:
            total_due += float(item.amount_due)
            total_paid_dynamic += float(item.amount_paid)
            if item.amount_due > 0:
                latest_fee.breakdown_items.append(
                    {
                        "label": item.name,
                        "value": item.amount_due,
                        "paid": item.amount_paid,
                        "balance": item.balance,
                        "id": item.id,
                    }
                )
                if item.balance > 0:
                    latest_fee.payment_options.append(
                        {
                            "label": item.name,
                            "balance": float(item.balance),
                            "dynamic_item_id": item.id,
                            "paid_field": item.name,
                        }
                    )

        # Real balance from dynamic items
        real_balance = total_due - total_paid_dynamic
        if real_balance < 0:
            real_balance = 0

        # Fallback if no dynamic items but has total_amount
        if not dynamic_items.exists() and latest_fee.total_amount > 0:
            real_balance = float(latest_fee.total_amount) - float(real_total_paid)
            latest_fee.breakdown_items = [
                {
                    "label": "Total Fees",
                    "value": latest_fee.total_amount,
                    "paid": real_total_paid,
                    "balance": real_balance,
                }
            ]
            if real_balance > 0:
                latest_fee.payment_options = [
                    {
                        "label": "Outstanding Balance",
                        "balance": real_balance,
                        "dynamic_item_id": None,
                        "paid_field": "amount_paid",
                    }
                ]

    if not fees_qs.exists() and (year_id or term_number):
            latest_fee = None
            real_balance = 0
            real_total_paid = 0

    context = {
        'fees': fees_qs,
        'latest_fee': latest_fee,
        'total_fee': float(latest_fee.total_amount) if latest_fee else 0,
        'balance': real_balance if latest_fee else 0,
        'amount_paid': float(real_total_paid) if latest_fee else 0,
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
    if request.user.role != "parent":
        return redirect("accounts:dashboard")

    school = request.user.school  # PARENT SCHOOL - ISOLATION ROOT

    links = ParentStudentLink.objects.filter(
        parent=request.user, student__school=school
    ).select_related("student", "student__school_class", "student__school")

    children = [link.student for link in links]
    children_count = len(children)

    child_id = request.GET.get("child_id") or request.session.get("selected_child_id")
    selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
    if not selected_child and children:
        selected_child = children[0]
    if selected_child:
        request.session["selected_child_id"] = selected_child.id
        if selected_child.school_id != school.id:
            messages.error(request, "Access denied.")
            return redirect("accounts:parent_dashboard")

    selected_year_id = request.GET.get("year")
    term_id = request.GET.get("term_id") or request.GET.get("term")

    years = (
        AcademicYear.objects.filter(school=school).order_by("-name") if school else []
    )
    all_terms = Term.objects.none()

    if selected_child and school:
        all_terms_qs = Term.objects.filter(
            school=school, result__student=selected_child, result__status="published"
        ).distinct()

        if selected_year_id:
            all_terms_qs = all_terms_qs.filter(academic_year_id=selected_year_id)

        all_terms = all_terms_qs.order_by("-start_date")

    selected_term = (
        all_terms.filter(id=term_id).first() if term_id else all_terms.first()
    )

    results = []
    summary = None
    term_data = None
    remark = None

    if selected_child and selected_term:
        results = (
            Result.objects.filter(
                student=selected_child,
                student__school=school,
                term=selected_term,
                status="published",
            )
            .select_related("subject", "term", "term__academic_year")
            .order_by("subject__name")
        )

        for r in results:
            r.calc_ca = float(r.class_score or 0)
            r.calc_exam = float(r.exam_converted_score or 0)
            r.calc_total = float(r.total_score or 0)
            grading = GradingScale.objects.filter(
                school=school,
                term=selected_term,
                min_score__lte=r.total_score,
                max_score__gte=r.total_score,
            ).first()

            if not grading:
                grading = GradingScale.objects.filter(
                    school=school,
                    term__isnull=True,
                    min_score__lte=r.total_score,
                    max_score__gte=r.total_score,
                ).first()

            r.calc_grade = grading.grade if grading else "-"
            r.calc_remark = grading.remark if grading else "-"

        remark = StudentRemark.objects.filter(
            student=selected_child, student__school=school, term=selected_term
        ).first()

        class_score = sum(float(r.calc_ca or 0) for r in results)
        exam_score = sum(float(r.calc_exam or 0) for r in results)
        total_score = class_score + exam_score
        n = len(results) or 1
        percentage = round(total_score / n, 1)

        term_name = getattr(selected_term, "name", str(selected_term))
        academic_year_obj = getattr(selected_term, "academic_year", None)
        summary_qs = StudentTermSummary.objects.filter(
            student=selected_child, student__school=school, term=term_name
        )
        if academic_year_obj:
            summary_qs = summary_qs.filter(academic_year=academic_year_obj)
        summary = summary_qs.first()

        position = 1
        class_size = 1
        overall_grade = "_"
        overall_remark = "_"

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
                class_size = (
                    StudentTermSummary.objects.filter(
                        school_class=summary.school_class,
                        school_class__school=school,
                        term=summary.term,
                        academic_year=summary.academic_year,
                    ).count()
                    or 1
                )
        else:
            grading = GradingScale.objects.filter(
                school=school,
                term=selected_term,
                min_score__lte=percentage,
                max_score__gte=percentage,
            ).first()

            if not grading:
                grading = GradingScale.objects.filter(
                    school=school,
                    term__isnull=True,
                    min_score__lte=percentage,
                    max_score__gte=percentage,
                ).first()

            if grading:
                overall_grade = grading.grade
                overall_remark = grading.remark

        term_data = {
            "term": selected_term,
            "results": results,
            "grand_class_score": class_score,
            "grand_exam_score": exam_score,
            "grand_total_score": round(total_score, 1),
            "grand_percentage": percentage,
            "overall_grade": overall_grade,
            "overall_remark": overall_remark,
            "class_position": position,
            "class_size": class_size,
            "summary": summary,
        }

        start = selected_term.start_date
        today = timezone.now().date()
        end = selected_term.end_date if selected_term.end_date < today else today

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
                student=selected_child,
                student__school=school,
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
        times_absent = len(by_date) - times_present
        times_late = 0
        times_excused = 0
        days_opened = sum(
            1
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5
            and (start + timedelta(days=i)) not in holiday_dates
        )
        attendance_percentage = (
            round((times_present / days_opened * 100), 1) if days_opened else 0
        )

        term_data.update(
            {
                "days_opened": days_opened,
                "times_present": times_present,
                "times_absent": times_absent,
                "times_late": times_late,
                "times_excused": times_excused,
                "attendance_percentage": attendance_percentage,
            }
        )

    context = {
        "children": children,
        "children_count": children_count,
        "selected_child": selected_child,
        "terms": all_terms,
        "years": years,
        "selected_term": selected_term,
        "selected_year": selected_year_id or "",
        "term_data": term_data,
        "summary": summary,
        "school": school,
        "remark": remark,
    }
    return render(request, "accounts/view_child_results.html", context)


@login_required
def view_child_fees(request):
    if request.user.role != "parent":
        return redirect("accounts:dashboard")

    school = request.user.school  # ISOLATION

    links = ParentStudentLink.objects.filter(
        parent=request.user, student__school=school
    ).select_related("student")
    children = [link.student for link in links]

    selected_child = None
    if len(children) == 1:
        selected_child = children[0]
    elif children:
        child_id = request.GET.get("child") or request.session.get("selected_child_id")
        selected_child = (
            next((c for c in children if str(c.id) == str(child_id)), None)
            or children[0]
        )

    if selected_child:
        request.session["selected_child_id"] = selected_child.id

    # ✅ FIX: Define all variables BEFORE if, so they always exist
    student_fees = []
    transactions = []
    all_payment_options = []
    totals = {"total_amount": 0, "total_paid": 0, "total_balance": 0}
    latest_fee = None
    all_years = []
    selected_year = None

    if selected_child:
        # Transactions - school isolated
        transactions = (
            PaymentTransaction.objects.filter(
                student=selected_child, school=school, is_voided=False
            )
            .prefetch_related("items")
            .order_by("-created_at")
        )
        # Year filter logic
        all_years = list(
            StudentFee.objects.filter(student=selected_child, school=school)
            .values_list("academic_year", flat=True)
            .distinct()
            .order_by("-academic_year")
        )

        selected_year = request.GET.get("year")

        base_qs = StudentFee.objects.filter(student=selected_child, school=school)

        if selected_year and selected_year != "all" and selected_year in all_years:
            student_fees_qs = base_qs.filter(academic_year=selected_year)
        elif selected_year == "all":
            student_fees_qs = base_qs
        else:
            latest_year = (
                base_qs.order_by("-id").values_list("academic_year", flat=True).first()
            )
            student_fees_qs = (
                base_qs.filter(academic_year=latest_year) if latest_year else base_qs
            )
            selected_year = latest_year

        student_fees = (
            student_fees_qs.select_related("term")
            .prefetch_related("dynamic_items")
            .order_by("academic_year", "term__term_number")
        )

        for fee in student_fees:
            fee.breakdown_items = []
            total_due = 0
            total_paid = 0
            for item in fee.dynamic_items.all():
                due = float(item.amount_due or 0)
                paid = float(item.amount_paid or 0)
                bal = max(0, due - paid)
                total_due += due
                total_paid += paid
                fee.breakdown_items.append(
                    {
                        "label": item.name,
                        "value": due,
                        "paid": paid,
                        "balance": bal,
                        "payment_type_id": item.payment_type_id,
                    }
                )
                if bal > 0.01:
                    all_payment_options.append(
                        {
                            "label": item.name,
                            "balance": bal,
                            "fee_id": fee.id,
                            "dynamic_item_id": item.id,
                            "term": str(fee.term),
                            "year": fee.academic_year,
                        }
                    )
            fee.total_amount_val = total_due or float(fee.total_amount or 0)
            fee.amount_paid_val = total_paid
            fee.balance_val = max(0, fee.total_amount_val - total_paid)

        totals["total_amount"] = sum(f.total_amount_val for f in student_fees)
        totals["total_paid"] = sum(f.amount_paid_val for f in student_fees)
        totals["total_balance"] = sum(f.balance_val for f in student_fees)
        latest_fee = student_fees[0] if student_fees else None

    return render(
        request,
        "accounts/view_child_fees.html",
        {
            "children": children,
            "selected_child": selected_child,
            "student_fees": student_fees,
            "totals": totals,
            "all_payment_options": all_payment_options,
            "transactions": transactions,
            "school": school,
            "show_switcher": len(children) > 1,
            "latest_fee": latest_fee,
            "all_years": all_years,
            "selected_year": selected_year,
        },
    )


@login_required
def view_child_progress(request):
    if request.user.role != "parent":
        return redirect("accounts:dashboard")

    def get_grading_scale(p, term):
        grading = GradingScale.objects.filter(
            school=school,
            term=term,
            min_score__lte=p,
            max_score__gte=p,
        ).first()

        if not grading:
            grading = GradingScale.objects.filter(
                school=school,
                term__isnull=True,
                min_score__lte=p,
                max_score__gte=p,
            ).first()

        return grading

    school = request.user.school  # PARENT SCHOOL - ISOLATION ROOT
    links = ParentStudentLink.objects.filter(
        parent=request.user, student__school=school
    ).select_related("student", "student__school", "student__school_class")

    children = [link.student for link in links]
    children_count = len(children)

    child_id = request.GET.get("child_id") or request.session.get("selected_child_id")
    selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
    if not selected_child and children:
        selected_child = children[0]
    if selected_child:
        request.session["selected_child_id"] = selected_child.id
        # DOUBLE CHECK SCHOOL
        if selected_child.school_id != school.id:
            messages.error(request, "Access denied.")
            return redirect("accounts:parent_dashboard")

    selected_link = next(
        (l for l in links if l.student_id == getattr(selected_child, "id", None)), None
    )

    selected_year_id = request.GET.get("year")
    selected_term_id = request.GET.get("term") or request.GET.get("term_id")

    years = []
    terms = []
    terms_data = []

    if selected_child:
        years = AcademicYear.objects.filter(school=school).order_by("-name")
        terms_qs = Term.objects.filter(school=school).order_by("-start_date")
        if selected_year_id:
            terms_qs = terms_qs.filter(academic_year_id=selected_year_id)
        terms = terms_qs

        qs = Result.objects.filter(
            student=selected_child, student__school=school, status="published"
        ).select_related("subject", "term", "term__academic_year")

        if selected_year_id:
            qs = qs.filter(term__academic_year_id=selected_year_id)
        if selected_term_id:
            qs = qs.filter(term_id=selected_term_id)

        by_term = defaultdict(list)
        for r in qs:
            by_term[r.term].append(r)

        for term_obj, results in by_term.items():

            def ca_max(t):
                return float(getattr(t, "ca_total", 0) or 0)

            def exam_max(t):
                return float(getattr(t, "exam_total", 0) or 0)

            class_score = sum(float(r.class_score or 0) for r in results)
            exam_score = sum(float(r.exam_score or 0) for r in results)
            class_max = sum(ca_max(r.term) for r in results)
            exam_max_sum = sum(exam_max(r.term) for r in results)

            total_score = class_score + exam_score
            total_max = class_max + exam_max_sum
            percentage = round(total_score / total_max * 100, 2) if total_max else 0
            grading = get_grading_scale(percentage, term_obj)

            overall_grade = grading.grade if grading else "-"
            overall_remark = grading.remark if grading else "-"

            term_name = getattr(term_obj, "name", str(term_obj))
            academic_year_obj = getattr(term_obj, "academic_year", None)

            summary_qs = StudentTermSummary.objects.filter(
                student=selected_child, student__school=school, term=term_name
            )
            if academic_year_obj:
                summary_qs = summary_qs.filter(academic_year=academic_year_obj)

            summary = summary_qs.first()
            position = 1
            class_size = 1
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
                    class_size = (
                        StudentTermSummary.objects.filter(
                            school_class=summary.school_class,
                            school_class__school=school,
                            term=summary.term,
                            academic_year=summary.academic_year,
                        ).count()
                        or 1
                    )

            terms_data.append(
                {
                    "term": term_obj,
                    "results": results,
                    "grand_class_score": class_score,
                    "grand_class_max": class_max,
                    "grand_exam_score": exam_score,
                    "grand_exam_max": exam_max_sum,
                    "grand_total_score": total_score,
                    "grand_total_max": total_max,
                    "grand_percentage": percentage,
                    "overall_grade": overall_grade,
                    "overall_remark": overall_remark,
                    "class_position": position,
                    "class_size": class_size,
                    "term_status": "Published",
                }
            )

    context = {
        "children": children,
        "children_count": children_count,
        "selected_child": selected_child,
        "relationship": getattr(selected_link, "relationship", "Child"),
        "terms": terms_data,
        "years": years,
        "terms_list": terms,
        "selected_year": selected_year_id or "",
        "selected_term": selected_term_id or "",
        "school": school,
    }
    return render(request, "accounts/view_child_progress.html", context)


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
    if request.user.role not in [
        "admin",
        "proprietor",
        "proprietress",
        "headmaster",
        "headmistress",
    ]:
        return redirect("accounts:dashboard")

    school = request.user.school
    if not school:
        messages.error(request, "Your school was not found.")
        return redirect("accounts:dashboard")

    setting, created = SystemSetting.objects.get_or_create(
        school=school,
        defaults={
            "site_name": school.name,
            "max_students": 30,
        },
    )

    if request.method == "POST":
        site_name = request.POST.get("site_name", "").strip()
        max_students = request.POST.get("max_students", "30").strip()

        if not site_name:
            messages.error(request, "Site name cannot be empty.")
            return redirect("accounts:system_settings")

        try:
            max_students = int(max_students)
            if max_students < 1:
                raise ValueError
        except ValueError:
            messages.error(
                request, "Maximum students per class must be greater than 0."
            )
            return redirect("accounts:system_settings")

        setting.site_name = site_name
        setting.max_students = max_students
        setting.save()

        messages.success(request, "System settings updated successfully.")
        return redirect("accounts:system_settings")

    backup_dir = settings.BASE_DIR / "backups" / f"school_{school.id}"

    if backup_dir.exists():
        backups = sorted(
            backup_dir.glob("school_backup_*.json"),
            key=lambda file: file.stat().st_mtime,
            reverse=True,
        )
    else:
        backups = []

    return render(
        request,
        "accounts/system_settings.html",
        {
            "school": school,
            "setting": setting,
            "backups": backups,
        },
    )


# ----------------------------
# REPORTS
# ----------------------------
@login_required
def student_performance_report(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress',
    'hod',
    'teacher',
]:
        return redirect('accounts:dashboard')

    school = request.user.school

    # FIX 1: Teacher sees only his classes
    if request.user.role == 'teacher':
        assigned_class_ids = [request.user.class_teacher_of_id] if request.user.class_teacher_of_id else []
        classes = SchoolClass.objects.filter(
            school=school,
            id__in=assigned_class_ids
        )
    else:
        # admin = headmaster = hod sees ALL - same permission
        classes = SchoolClass.objects.filter(school=school)

    academic_years = AcademicYear.objects.filter(school=school).order_by('-name')
    terms = Term.objects.filter(school=school).order_by('-start_date')

    selected_class = request.GET.get('class')
    selected_term = request.GET.get('term')
    selected_year = request.GET.get('year')
    is_print = request.GET.get('print') == '1'

    # FIX 1b: Block teacher if he tries to view class he doesn't teach
    if request.user.role == 'teacher' and selected_class and selected_class!= 'all':
        if int(selected_class) not in list(assigned_class_ids):
            messages.error(request, "You are not assigned to this class.")
            return redirect('accounts:student_performance_report')

    term_obj = None
    if selected_term and selected_term!= 'all':
        term_obj = Term.objects.filter(id=selected_term, school=school).first()

    year_obj = None
    if selected_year and selected_year!= 'all':
        year_obj = AcademicYear.objects.filter(id=selected_year, school=school).first()

    # FIX 2: Students query - teacher only his students
    students_qs = User.objects.filter(role='student', school=school, is_active=True)
    if request.user.role == 'teacher':
        students_qs = students_qs.filter(school_class_id__in=assigned_class_ids)

    if selected_class and selected_class!= 'all':
        students_qs = students_qs.filter(school_class_id=selected_class)

    temp_list = []
    for student in students_qs.select_related('school_class'):
        results_filter = {'student': student, 'status': 'published', 'school': school}
        if term_obj:
            results_filter['term'] = term_obj
        if year_obj:
            results_filter['academic_year'] = year_obj

        results = Result.objects.filter(**results_filter)

        if results.count() == 0:
            if not selected_class or selected_class == 'all':
                continue
            avg, total, grade, remark = 0, 0, '-', 'No Results'
        else:
                    total = 0

                    for r in results:
                        try:
                            cs = float(r.class_score or 0)
                        except:
                            cs = 0

                        try:
                            es = float(r.exam_converted_score or 0)
                        except:
                            es = 0

                        total += cs + es

                    avg = round(total / results.count(), 2)

                    grading = None

                    # Use the selected term when one was selected
                    if term_obj:
                        grading = GradingScale.objects.filter(
                            school=school,
                            term=term_obj,
                            min_score__lte=avg,
                            max_score__gte=avg,
                        ).first()

                    # Fall back to school-wide grading
                    if not grading:
                        grading = GradingScale.objects.filter(
                            school=school,
                            term__isnull=True,
                            min_score__lte=avg,
                            max_score__gte=avg,
                        ).first()

                    grade = grading.grade if grading else "-"
                    remark = grading.remark if grading else "-"

        temp_list.append({
            'student': student,
            'school_class': student.school_class,
            'term_total': total,
            'term_average': avg,
            'term_grade': grade,
            'term_remark': remark,
        })

    grouped_summaries = None
    summaries = []

    if not selected_class or selected_class == 'all':
        grouped = defaultdict(list)
        for item in temp_list:
            if item['school_class']:
                grouped[item['school_class']].append(item)

        grouped_summaries = []
        for school_class, items in sorted(grouped.items(), key=lambda x: x[0].name if x[0] else ""):
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
            summaries.extend(class_summaries)

        summaries = sorted(summaries, key=lambda x: x.term_average, reverse=True)
    else:
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

    if is_print:
        class_name = SchoolClass.objects.filter(id=selected_class, school=school).first().name if selected_class and selected_class!= 'all' else "All Classes"
        term_name = str(term_obj) if term_obj else "All Terms"
        year_name = str(year_obj) if term_obj else "All Years"
        return render(request, 'accounts/student_performance_print.html', {
            'summaries': summaries,
            'grouped_summaries': grouped_summaries,
            'total_students': total_students,
            'class_name': class_name, 'term_name': term_name, 'year_name': year_name,
            'school': school, 'avg_score': avg_score, 'pass_rate': pass_rate,
        })

    context = {
        'classes': classes, 'terms': terms, 'academic_years': academic_years,
        'selected_class': selected_class or 'all',
        'selected_term': selected_term or 'all',
        'selected_year': selected_year or 'all',
        'grouped_summaries': grouped_summaries,
        'summaries': summaries if not selected_class or selected_class == 'all' else Paginator(summaries, 10).get_page(request.GET.get('page')),
        'total_students': total_students, 'avg_score': avg_score,
        'pass_rate': pass_rate, 'top_student': top_student,
    }

    if selected_class and selected_class!= 'all':
        context['summaries'] = Paginator(summaries, 10).get_page(request.GET.get('page'))

    return render(request, 'accounts/student_performance_report.html', context)


@login_required
def student_attendance_report(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress',
    'hod',
    'teacher',
]:
        return redirect('accounts:home')
    school = request.user.school

    # TEACHER: ONLY THEIR CLASS TEACHER CLASS
    if request.user.role == 'teacher':
        assigned_classes_ids = SchoolClass.objects.filter(
            school=school,
            class_teacher=request.user
        ).values_list('id', flat=True)

        classes = SchoolClass.objects.filter(
            school=school,
            id__in=assigned_classes_ids
        )
    else:
        classes = SchoolClass.objects.filter(school=school)

    academic_years = AcademicYear.objects.filter(school=school).order_by('-name')
    terms = Term.objects.filter(school=school).order_by('academic_year__name', 'term_number')

    selected_class = request.GET.get('class')
    selected_term = request.GET.get('term')
    selected_year = request.GET.get('year')
    is_print = request.GET.get('print') == '1'

    s_class = selected_class
    s_term = selected_term
    s_year = selected_year

    if not selected_class or selected_class in ['None','null','all','']:
        selected_class = None
    if not selected_term or selected_term in ['None','null','all','']:
        selected_term = None
    if not selected_year or selected_year in ['None','null','all','']:
        selected_year = None

    sessions = AttendanceSession.objects.filter(school_class__school=school).select_related('school_class','subject','teacher').prefetch_related('records__student').order_by('-date')

    # FORCE TEACHER FILTER
    if request.user.role == 'teacher':
        sessions = sessions.filter(school_class_id__in=assigned_classes_ids)

    if selected_class:
        try: sessions = sessions.filter(school_class_id=int(selected_class))
        except: pass
    if selected_term:
        try: sessions = sessions.filter(term_id=int(selected_term))
        except: pass
    if selected_year:
        try: sessions = sessions.filter(term__academic_year_id=int(selected_year))
        except: pass

    daily_map = defaultdict(list)
    for s in sessions:
        only_date = s.date.date() if hasattr(s.date, 'date') else s.date
        daily_map[(only_date, s.school_class_id)].append(s)

    merged_days = []
    for (date, class_id), sess_list in sorted(daily_map.items()):
        school_class = sess_list[0].school_class
        student_status = {}
        has_any = False
        for sess in sess_list:
            recs = list(sess.records.all())
            if recs: has_any = True
            for r in recs: student_status.setdefault(r.student.id, []).append(r.status)
        if not has_any or not student_status: continue
        p = sum(1 for v in student_status.values() if 'P' in v)
        a = len(student_status) - p
        merged_days.append({'date': date, 'school_class': school_class, 'present_count': p, 'absent_count': a, 'total': p+a})

    total_present = sum(d['present_count'] for d in merged_days)
    total_absent = sum(d['absent_count'] for d in merged_days)
    total_all = total_present + total_absent
    attendance_rate = round(total_present/total_all*100,1) if total_all else 0

    grouped_dict = defaultdict(list)
    for d in merged_days: grouped_dict[d['school_class']].append(d)
    grouped_list = []
    for klass, days in grouped_dict.items():
        grouped_list.append({'klass': klass, 'days': sorted(days, key=lambda x: x['date']), 'total_days': len(days)})
    grouped_list = sorted(grouped_list, key=lambda x: x['klass'].name)

    obj_class = SchoolClass.objects.filter(id=s_class).first() if s_class and s_class not in ['all','None'] else None
    obj_term = Term.objects.filter(id=s_term).first() if s_term and s_term not in ['all','None'] else None
    obj_year = AcademicYear.objects.filter(id=s_year).first() if s_year and s_year not in ['all','None'] else None

    if is_print:
        return render(request, 'accounts/student_attendance_print.html', {
            'school': school, 'merged_days': merged_days,
            'total_present': total_present, 'total_absent': total_absent, 'attendance_rate': attendance_rate,
            'grouped_by_class': grouped_list,
            'selected_class_obj': obj_class,
            'class_name': obj_class.name if obj_class else "All Classes",
            'term_name': str(obj_term) if obj_term else "All Terms",
            'year_name': obj_year.name if obj_year else "All Years",
        })

    return render(request, 'accounts/student_attendance_report.html', {
        'school': school,
        'sessions': Paginator(merged_days, 10).get_page(request.GET.get('page')),
        'classes': classes, 'terms': terms, 'academic_years': academic_years,
        'selected_class': str(s_class) if s_class else 'all',
        'selected_term': str(s_term) if s_term else 'all',
        'selected_year': str(s_year) if s_year else 'all',
        'total_present': total_present, 'total_absent': total_absent,
        'attendance_rate': attendance_rate, 'grouped_by_class': grouped_list,
        'selected_class_obj': SchoolClass.objects.filter(id=s_class).first() if s_class else None,
    })


@login_required
def teacher_attendance_report(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        messages.error(request, "You do not have permission to view teacher attendance.")
        return redirect('accounts:home')
    school = request.user.school
    if not school:
        messages.error(request, "Your account is not assigned to any school.")
        return redirect('accounts:home')

    # === FILTERS ===
    academic_years = AcademicYear.objects.filter(school=school).order_by('-name')
    terms = Term.objects.filter(school=school).order_by('academic_year__name', 'term_number')
    
    selected_year = request.GET.get('year')
    selected_term = request.GET.get('term')
    if not selected_year or selected_year in ['None','null','all','']: selected_year = None
    if not selected_term or selected_term in ['None','null','all','']: selected_term = None

    # Determine active term based on filter
    if selected_term:
        active_term = Term.objects.filter(id=int(selected_term), school=school).first()
    elif selected_year:
        active_term = Term.objects.filter(academic_year_id=int(selected_year), school=school, is_active=True).first() or Term.objects.filter(academic_year_id=int(selected_year), school=school).first()
    else:
        active_term = Term.objects.filter(school=school, is_active=True).first()

    # Week selection
    week_str = request.GET.get('attendance_week')
    selected_date = parse_date(week_str) if week_str else timezone.localdate()
    if not selected_date: selected_date = timezone.localdate()
    if selected_date.weekday() >= 5:
        selected_date = selected_date - timedelta(days=selected_date.weekday() - 4)
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_end = week_start + timedelta(days=4)

    # Term limits
    term_start = active_term.start_date if active_term else week_start
    term_end = active_term.end_date if active_term else week_end

    # Clamp week_start inside term
    if week_start < term_start: week_start = term_start
    if week_start > term_end: week_start = term_end - timedelta(days=4)
    week_end = week_start + timedelta(days=4)
    if week_end > term_end: week_end = term_end

    # Academic week number
    attendance_week = 1
    if active_term:
        current_day = term_start
        while current_day < week_start:
            if current_day.weekday() == 4: attendance_week += 1
            current_day += timedelta(days=1)

    previous_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)
    is_first_week = week_start <= term_start
    is_last_week = week_end >= term_end
    if is_first_week: previous_week = week_start
    if is_last_week: next_week = week_start

    working_days = []
    current = week_start
    while current <= week_end:
        if current.weekday() < 5: working_days.append(current)
        current += timedelta(days=1)

    teachers = User.objects.filter(school=school, role='teacher').order_by('first_name','last_name')
    attendance_records = TeacherAttendance.objects.filter(school=school, date__range=[week_start, week_end]).select_related('teacher')
    attendance_map = defaultdict(dict)
    for r in attendance_records: attendance_map[r.teacher_id][r.date] = r

    report = []
    for teacher in teachers:
        days=[]; present=absent=late=sick=excused=leave=0
        for day in working_days:
            att = attendance_map.get(teacher.id, {}).get(day)
            status = att.status if att else None
            if status=="P": present+=1
            elif status=="A": absent+=1
            elif status=="L": late+=1
            elif status=="E": excused+=1
            elif status=="S": sick+=1
            elif status=="LV": leave+=1
            days.append({"date": day, "attendance": att, "status": status})
        total_days = len(working_days)
        rate = round(((present+late)/total_days)*100,1) if total_days else 0
        report.append({"teacher": teacher, "days": days, "present": present, "absent": absent, "late": late, "sick": sick, "leave": leave, "excused": excused, "attendance_rate": rate})

    total_teachers = teachers.count()
    base_qs = TeacherAttendance.objects.filter(school=school, date__range=[week_start, week_end])
    total_present = base_qs.filter(status="P").count()
    total_absent = base_qs.filter(status="A").count()
    total_late = base_qs.filter(status="L").count()
    total_sick = base_qs.filter(status="S").count()
    total_excused = base_qs.filter(status="E").count()
    total_leave = base_qs.filter(status="LV").count()

    return render(request, "accounts/teacher_attendance_report.html", {
        "school": school, "report": report, "working_days": working_days,
        "report_week_start": week_start, "report_week_end": week_end,
        "report_previous_week": previous_week, "report_next_week": next_week,
        "is_first_week": is_first_week, "is_last_week": is_last_week,
        "total_teachers": total_teachers, "total_present": total_present, "total_absent": total_absent,
        "total_late": total_late, "total_excused": total_excused, "total_sick": total_sick, "total_leave": total_leave,
        "attendance_week": attendance_week,
        "academic_years": academic_years, "terms": terms,
        "selected_year": str(selected_year) if selected_year else 'all',
        "selected_term": str(selected_term) if selected_term else 'all',
        "active_term": active_term,
    })

@login_required
def teacher_attendance_print(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        messages.error(
            request,
            "You do not have permission to print teacher attendance."
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
    # SELECTED FILTERS
    # =========================

    selected_year = request.GET.get('year')
    selected_term = request.GET.get('term')

    if selected_year in [None, '', 'None', 'null', 'all']:
        selected_year = None

    if selected_term in [None, '', 'None', 'null', 'all']:
        selected_term = None

    # =========================
    # DETERMINE REPORT PERIOD
    # =========================

    if selected_term:
        # EXACT TERM SELECTED
        term = Term.objects.filter(
            id=int(selected_term),
            school=school
        ).select_related('academic_year').first()

        if not term:
            messages.error(request, "Selected term was not found.")
            return redirect('accounts:teacher_attendance_report')

        report_start = term.start_date
        report_end = term.end_date

        year_name = term.academic_year.name
        term_name = str(term)

    elif selected_year:
        # YEAR SELECTED, ALL TERMS IN THAT YEAR
        year = AcademicYear.objects.filter(
            id=int(selected_year),
            school=school
        ).first()

        if not year:
            messages.error(request, "Selected academic year was not found.")
            return redirect('accounts:teacher_attendance_report')

        year_terms = Term.objects.filter(
            academic_year=year
        ).order_by('start_date')

        if not year_terms.exists():
            messages.error(
                request,
                "No terms were found for the selected academic year."
            )
            return redirect('accounts:teacher_attendance_report')

        report_start = year_terms.order_by('start_date').first().start_date
        report_end = year_terms.order_by('-end_date').first().end_date

        year_name = year.name
        term_name = "All Terms"

    else:
        # NO FILTERS
        # =========================
        # ALL YEARS / ALL TERMS
        # =========================

        all_terms = Term.objects.filter(
            school=school
        ).order_by('start_date')

        if not all_terms.exists():
            messages.error(
                request,
                "No academic terms were found."
            )
            return redirect('accounts:teacher_attendance_report')

        report_start = all_terms.first().start_date
        report_end = all_terms.last().end_date

        year_name = "All Years"
        term_name = "All Terms"

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
        date__range=[report_start, report_end]
    )

    # =========================
    # BUILD TEACHER SUMMARY
    # =========================

    report = []

    for teacher in teachers:

        teacher_records = attendance_records.filter(
            teacher=teacher
        )

        present = teacher_records.filter(status="P").count()
        absent = teacher_records.filter(status="A").count()
        late = teacher_records.filter(status="L").count()
        leave = teacher_records.filter(status="LV").count()
        sick = teacher_records.filter(status="S").count()
        excused = teacher_records.filter(status="E").count()

        total_recorded = (
            present +
            absent +
            late +
            leave +
            sick +
            excused
        )

        attendance_rate = (
            round(
                ((present + late) / total_recorded) * 100,
                1
            )
            if total_recorded
            else 0
        )

        report.append({
            "teacher": teacher,
            "present": present,
            "absent": absent,
            "late": late,
            "leave": leave,
            "sick": sick,
            "excused": excused,
            "total_recorded": total_recorded,
            "attendance_rate": attendance_rate,
        })

    # =========================
    # SUMMARY TOTALS
    # =========================

    total_teachers = teachers.count()

    total_present = attendance_records.filter(
        status="P"
    ).count()

    total_absent = attendance_records.filter(
        status="A"
    ).count()

    total_late = attendance_records.filter(
        status="L"
    ).count()

    total_leave = attendance_records.filter(
        status="LV"
    ).count()

    total_sick = attendance_records.filter(
        status="S"
    ).count()

    total_excused = attendance_records.filter(
        status="E"
    ).count()

    # =========================
    # PRINT
    # =========================

    return render(
        request,
        "accounts/teacher_attendance_print.html",
        {
            "school": school,
            "report": report,

            "total_teachers": total_teachers,
            "total_present": total_present,
            "total_absent": total_absent,
            "total_late": total_late,
            "total_leave": total_leave,
            "total_sick": total_sick,
            "total_excused": total_excused,

            "year_name": year_name,
            "term_name": term_name,

            "report_start": report_start,
            "report_end": report_end,
        }
    )


@login_required
def view_reports(request):
    if request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
    "teacher",
]:
        return redirect("accounts:dashboard")

    school = request.user.school

    # TEACHER FILTER
    if request.user.role == "teacher":
        assigned_class_ids = TeacherSubjectClass.objects.filter(
            teacher=request.user, school=school
        ).values_list("school_class_id", flat=True)

        total_students = User.objects.filter(
            school=school, school_class_id__in=assigned_class_ids
        ).count()
        total_classes = SchoolClass.objects.filter(
            school=school, id__in=assigned_class_ids
        ).count()
        performance_reports = StudentTermSummary.objects.filter(
            school_class_id__in=assigned_class_ids
        ).count()
        attendance_sessions = AttendanceSession.objects.filter(
            school_class_id__in=assigned_class_ids
        ).count()
        last_performance = (
            StudentTermSummary.objects.filter(school_class_id__in=assigned_class_ids)
            .order_by("-date_created")
            .first()
        )
        last_attendance = (
            AttendanceSession.objects.filter(school_class_id__in=assigned_class_ids)
            .order_by("-date")
            .first()
        )
        total_teachers = 1  # or User.objects.filter(school=school, role='teacher').count() - but for teacher show 1
    else:
        total_students = User.objects.filter(school=school, role="student").count()
        total_teachers = User.objects.filter(school=school, role="teacher").count()
        total_classes = SchoolClass.objects.filter(school=school).count()
        performance_reports = StudentTermSummary.objects.filter(
            school_class__school=school
        ).count()
        attendance_sessions = AttendanceSession.objects.filter(
            school_class__school=school
        ).count()
        last_performance = (
            StudentTermSummary.objects.filter(school_class__school=school)
            .order_by("-date_created")
            .first()
        )
        last_attendance = (
            AttendanceSession.objects.filter(school_class__school=school)
            .order_by("-date")
            .first()
        )

    context = {
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_classes": total_classes,
        "performance_reports": performance_reports,
        "attendance_sessions": attendance_sessions,
        "last_performance": last_performance,
        "last_attendance": last_attendance,
    }
    return render(request, "accounts/view_reports.html", context)


@login_required
def assign_student_class(request, student_id):

    if request.user.role not in [
        "admin",
        "proprietor",
        "proprietress",
        "headmaster",
        "headmistress"
    ]:
        return redirect('accounts:dashboard')

    student = get_object_or_404(User, id=student_id, role='student', school=request.user.school)

    classes = SchoolClass.objects.filter(school=request.user.school)

    if request.method == 'POST':
        class_id = request.POST.get('class_id')

        if class_id:
            selected_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)

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
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        return redirect('accounts:dashboard')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Students"

    ws.append([
        "First Name", "Last Name", "Username", "Email",
        "Phone", "Student Number", "Class", "Subjects"
    ])

    students = User.objects.filter(
        role='student', 
        school=request.user.school
    ).select_related('school_class')

    for student in students:
        subjects = ", ".join([
            ssc.subject.name for ssc in 
            student.studentsubjectclass_set.filter(school=request.user.school)
        ])
        
        ws.append([
            student.first_name,
            student.last_name,
            student.username,
            student.email or "",
            student.phone or "",
            student.student_number or "",
            student.school_class.name if student.school_class else "Not assigned",
            subjects if subjects else "Not assigned",
        ])

    # Prepare response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=students.xlsx'
    wb.save(response)
    return response


# EDIT CLASS
@login_required
def edit_class(request, pk):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        return redirect('accounts:dashboard')

    class_obj = get_object_or_404(SchoolClass, id=pk, school=request.user.school)
    
    if request.method == "POST":
        name = request.POST.get("name")
        class_obj.name = name
        class_obj.allows_student_login = 'allows_student_login' in request.POST
        class_obj.save()
        messages.success(request, "Class updated successfully ✅")
        return redirect('accounts:admin_classes')
        
    return render(request, 'accounts/edit_class.html', {'class': class_obj})

# DELETE CLASS
@login_required
def delete_class(request, pk):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        return redirect('accounts:dashboard')
    class_obj = get_object_or_404(SchoolClass, id=pk, school=request.user.school)
    
    if class_obj.students.filter(school=request.user.school).exists():
        messages.error(request, "Cannot delete class. It has students ❌")
        return redirect('accounts:admin_classes')
        
    class_obj.delete()
    messages.success(request, "Class deleted successfully 🗑️")
    return redirect('accounts:admin_classes')

# CLASS DETAILS
@login_required
def class_detail(request, pk):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress',
    'teacher',
]:
        return redirect('accounts:dashboard')
    
    class_obj = get_object_or_404(SchoolClass, pk=pk, school=request.user.school)
    
    # TEACHER CHECK - only his classes
    if request.user.role == 'teacher':
        assigned_class_ids = TeacherSubjectClass.objects.filter(
            teacher=request.user, school=request.user.school
        ).values_list('school_class_id', flat=True)
        if class_obj.id not in assigned_class_ids:
            messages.error(request, "You are not assigned to this class ❌")
            return redirect('accounts:dashboard')

    students = User.objects.filter(school=request.user.school, role='student', school_class=class_obj)
    subjects = class_obj.subjects.all() 
    assignments = TeacherSubjectClass.objects.filter(
        school_class=class_obj, school_class__school=request.user.school
    )

    return render(request, 'accounts/class_detail.html', {
        'class_obj': class_obj,
        'students': students,
        'subjects': subjects,
        'assignments': assignments
    })

# ASING_TEACHER_SUBJECT
@login_required
def assign_teacher_subject(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        return redirect('accounts:dashboard')
    teachers = User.objects.filter(role='teacher', school=request.user.school)
    subjects = Subject.objects.filter(school=request.user.school)
    classes = SchoolClass.objects.filter(school=request.user.school)

    selected_class_id = request.GET.get("class")
    selected_class_obj = None

    if selected_class_id:
        selected_class_id = int(selected_class_id)
        selected_class_obj = get_object_or_404(
            SchoolClass, id=selected_class_id, school=request.user.school
        )

    if request.method == "POST":
        teacher_id = request.POST.get("teacher")
        subject_id = request.POST.get("subject")
        class_id = request.POST.get("class")

        teacher = get_object_or_404(User, id=teacher_id, role='teacher', school=request.user.school)
        subject = get_object_or_404(Subject, id=subject_id, school=request.user.school)
        school_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)

        exists = TeacherSubjectClass.objects.filter(
            school_class_id=class_id,
            subject_id=subject_id
        ).exists()

        if exists:
            messages.warning(request, "This subject is already assigned in this class ⚠️")
            return redirect(f"/assign-teacher-subject/?class={class_id}")

        TeacherSubjectClass.objects.create(
            teacher=teacher,
            subject=subject,
            school_class=school_class
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
@login_required
def admin_classes(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress',
    'hod',
    'teacher',
]:
        return redirect('accounts:dashboard')

    school = request.user.school

    # TEACHER - only his classes
    if request.user.role == 'teacher':
        assigned_class_ids = TeacherSubjectClass.objects.filter(
            teacher=request.user, school=school
        ).values_list('school_class_id', flat=True)
        classes = SchoolClass.objects.filter(school=school, id__in=assigned_class_ids)
    else:
        classes = SchoolClass.objects.filter(school=school)

    order = {
        'creche': 1, 'nursery': 2, 'kg': 3,
        'lower_primary': 4, 'upper_primary': 5,
        'jhs': 6, 'shs': 7
    }
    STAGE_LABELS = {
        'creche': 'Creche', 'nursery': 'Nursery', 'kg': 'KG',
        'lower_primary': 'Lower Primary', 'upper_primary': 'Upper Primary',
        'jhs': 'JHS', 'shs': 'SHS'
    }

    grouped = defaultdict(dict)

    for c in classes:
        if not c.id:
            continue
        # FIX - use User model, not students
        c.student_count = User.objects.filter(school=school, role='student', school_class=c).count()
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
        grouped[c.stage_label].setdefault(base_class, {"classes": []})
        grouped[c.stage_label][base_class]["classes"].append(c)

    grouped = dict(sorted(grouped.items(), key=lambda x: order.get(x[0].lower(), 999)))

    return render(request, 'accounts/admin_classes.html', {
        'grouped': grouped,
        'school': school,
    })
# ------------------------------
# ADD SUBJECT
# ----------------------------------
@login_required
def add_subject_to_class(request, class_id):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    if request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
]:
        return redirect("accounts:dashboard")

    # FIX - only your school
    teachers = User.objects.filter(
        role="teacher", school=request.user.school
    ).distinct()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Teachers"
    ws.append(
        [
            "First Name",
            "Last Name",
            "Username",
            "Email",
            "Phone",
            "School",
            "Classes",
            "Subjects",
        ]
    )

    for teacher in teachers:
        classes_list = []
        subjects_list = []
        for tc in teacher.teachersubjectclass_set.filter(school=request.user.school):
            if tc.school_class:
                classes_list.append(tc.school_class.name)
            if tc.subject:
                subjects_list.append(tc.subject.name)

        ws.append(
            [
                teacher.first_name,
                teacher.last_name,
                teacher.username,
                teacher.email,
                teacher.phone or "Not provided",
                teacher.school.name if teacher.school else "No school assigned",
                ", ".join(classes_list) or "Not assigned",
                ", ".join(subjects_list) or "Not assigned",
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=teachers.xlsx"
    wb.save(response)
    return response


# Edit_ASSIGNMENT
@login_required
def edit_assignment(request, id):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    return redirect("accounts:assigned_teachers_list")

@login_required
def add_assignment(request):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
    if request.method == 'POST':
        teacher_id = request.POST.get('teacher')
        class_id = request.POST.get('school_class')
        subject_id = request.POST.get('subject')

        # FIX - check school
        teacher = get_object_or_404(User, id=teacher_id, role='teacher', school=request.user.school)
        school_class = get_object_or_404(SchoolClass, id=class_id, school=request.user.school)
        subject = get_object_or_404(Subject, id=subject_id, school=request.user.school)

        exists = TeacherSubjectClass.objects.filter(
            school_class_id=class_id,
            subject_id=subject_id
        ).exists()

        if exists:
            messages.warning(request, "This subject is already assigned to a teacher in this class ⚠️")
            return redirect(f"/assign-teacher-subject/?class={class_id}")

        TeacherSubjectClass.objects.create(
            teacher=teacher,
            subject=subject,
            school_class=school_class
        )

        messages.success(request, "Assignment added successfully!")
        return redirect('accounts:add_assignment')

    return render(request, 'accounts/add_assignment.html')


@login_required
def remove_student_subject(request, student_id, subject_id):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:dashboard')
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
    if request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
]:
        return redirect("accounts:dashboard")

    if request.method == "POST":
        class_id = request.POST.get("class_id")
        subject_ids = request.POST.getlist("subjects")

        if class_id and subject_ids:
            # FIX - school filter
            school_class = get_object_or_404(
                SchoolClass, id=class_id, school=request.user.school
            )
            students = User.objects.filter(
                role="student", school=request.user.school, school_class=school_class
            )

            for student in students:
                for subject_id in subject_ids:
                    # FIX - check subject belongs to your school
                    subject = get_object_or_404(
                        Subject, id=subject_id, school=request.user.school
                    )
                    StudentSubjectClass.objects.get_or_create(
                        student=student, subject=subject, school_class=school_class
                    )
            messages.success(
                request, f"Subjects added to all students in {school_class.name} ✅"
            )
            return redirect(request.META.get("HTTP_REFERER", "/"))

    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def parent_dashboard(request):
    if request.user.role != "parent":
        return redirect("accounts:dashboard")

    school = request.user.school  # parent school
    links = ParentStudentLink.objects.filter(
        parent=request.user, student__school=school, student__is_active=True  # ISOLATED
    ).select_related("student", "student__school_class", "student__school")

    children = [link.student for link in links]

    child_id = request.GET.get("child_id") or request.session.get("selected_child_id")
    selected_child = next((c for c in children if str(c.id) == str(child_id)), None)
    if not selected_child and children:
        selected_child = children[0]

    if selected_child:
        request.session["selected_child_id"] = selected_child.id

    active_term = Term.objects.filter(school=school, is_active=True).first()  # ISOLATED

    announcement_count = 0
    fees_status = {"text": "No data", "color": "secondary", "amount": 0, "balance": 0}
    academic_status = {
        "text": "No data",
        "color": "secondary",
        "score": 0,
        "grade": "-",
    }
    attendance_pct = 0
    attendance_text = "0%"
    present_days = 0
    spent_days = 0
    total_days = 0
    attendance_display = "0/0"

    if children and active_term and selected_child:
        # 1. ANNOUNCEMENTS - ISOLATED
        today = timezone.now().date()
        all_anns = Announcement.objects.filter(
            school=school,
            created_at__date__gte=active_term.start_date,
            created_at__date__lte=today,
            is_active=True,
        ).order_by("-created_at")

        announcements_list = []
        for ann in all_anns:
            roles = ann.target_roles or []
            if isinstance(roles, str):
                import ast

                try:
                    roles = ast.literal_eval(roles)
                except:
                    roles = [r.strip(" '[]") for r in roles.split(",")]
            if any(r in roles for r in ["all", "parents", "students"]):
                if ann.created_at.weekday() < 5:
                    announcements_list.append(ann)

        announcements = announcements_list
        announcement_count = len(announcements)

        # 2. FEES - DYNAMIC + ISOLATED
        dyn_items = DynamicStudentFeeItem.objects.filter(
            school=school,
            student=selected_child,
            student_fee__term=active_term,
            student_fee__academic_year=str(
                active_term.academic_year.name
                if hasattr(active_term.academic_year, "name")
                else active_term.academic_year
            ),
        )
        if dyn_items.exists():
            total_due = sum(float(i.amount_due) for i in dyn_items)
            total_paid = sum(float(i.amount_paid) for i in dyn_items)
            balance = total_due - total_paid
            if balance <= 0:
                fees_status = {
                    "text": "All Paid ✓",
                    "color": "success",
                    "amount": 0,
                    "balance": 0,
                }
            else:
                fees_status = {
                    "text": f"GHS {balance:.2f}",
                    "color": "danger",
                    "amount": balance,
                    "balance": balance,
                }
        else:
            # Fallback to old StudentFee if no dynamic yet
            fee_record = StudentFee.objects.filter(
                school=school,
                student=selected_child,
                term=active_term,
            ).first()
            if fee_record:
                balance = fee_record.balance()
                if balance <= 0:
                    fees_status = {
                        "text": "All Paid ✓",
                        "color": "success",
                        "amount": 0,
                        "balance": 0,
                    }
                else:
                    fees_status = {
                        "text": f"GHS {balance}",
                        "color": "danger",
                        "amount": balance,
                        "balance": balance,
                    }

        # 3. ACADEMIC - ISOLATED
        results = Result.objects.filter(
            student=selected_child,
            student__school=school,
            term=active_term,
            status="published",
        )
        percentages = [r.percentage for r in results if r.percentage is not None]
        avg_score = sum(percentages) / len(percentages) if percentages else 0

        grading = GradingScale.objects.filter(
                    school=school,
                    term=active_term,
                    min_score__lte=avg_score,
                    max_score__gte=avg_score,
                ).first()

        if not grading:
            grading = GradingScale.objects.filter(
                school=school,
                term__isnull=True,
                min_score__lte=avg_score,
                max_score__gte=avg_score,
            ).first()

        if avg_score > 0 and grading:
            academic_status = {
                "text": grading.remark,
                "color": "success",
                "score": round(avg_score, 1),
                "grade": grading.grade,
            }
        elif avg_score > 0:
            academic_status = {
                "text": "No grading scale",
                "color": "secondary",
                "score": round(avg_score, 1),
                "grade": "-",
            }
        else:
            academic_status = {
                "text": "No grades",
                "color": "secondary",
                "score": 0,
                "grade": "-",
            }

        # 4. ATTENDANCE
        spent_days = calculate_school_days(
            active_term.start_date, today, selected_child.school
        )
        total_days = calculate_school_days(
            active_term.start_date, active_term.end_date, selected_child.school
        )

        records = AttendanceRecord.objects.filter(
            student=selected_child,
            student__school=school,
            session__date__gte=active_term.start_date,
            session__date__lte=today,
        ).values("session__date", "status")

        by_date = defaultdict(list)
        for r in records:
            by_date[r["session__date"]].append(r["status"])

        present_days = 0
        for date, statuses in by_date.items():
            if "P" in statuses:
                present_days += 1

        attendance_pct = (
            round((present_days / spent_days * 100), 1) if spent_days else 0
        )
        attendance_text = f"{attendance_pct}%"
        attendance_display = f"{present_days}/{spent_days}"

    context = {
        "parent": request.user,
        "children": children,
        "children_count": len(children),
        "selected_child": selected_child,
        "announcement_count": announcement_count,
        "fees_status": fees_status,
        "academic_status": academic_status,
        "attendance_pct": attendance_pct,
        "attendance_text": attendance_text,
        "active_term": active_term,
        "present_days": present_days,
        "spent_days": spent_days,
        "total_days": total_days,
        "attendance_display": attendance_display,
    }
    return render(request, "accounts/parent_dashboard.html", context)


@login_required
def student_profile(request, student_id=None):
    if request.user.role == "student":
        student = request.user
        school = student.school
    else:
        # PARENT / TEACHER / ADMIN - MUST CHECK SCHOOL
        school = request.user.school
        student = get_object_or_404(User, id=student_id, role="student", school=school)

    if request.user.role == "parent":
        if not ParentStudentLink.objects.filter(
            parent=request.user,
            student=student,
            student__school=school,  # <-- add isolation
            parent__school=school,
        ).exists():
            messages.error(request, "Access denied.")
            return redirect("accounts:parent_dashboard")

    active_term = Term.objects.filter(school=student.school, is_active=True).first()

    context = {
        "student": student,
        "active_term": active_term,
    }
    return render(request, "accounts/student_profile.html", context)


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

    # 3. GRADE — DYNAMIC
    grading = GradingScale.objects.filter(
        school=school,
        term=active_term,
        min_score__lte=average_score,
        max_score__gte=average_score,
    ).first()

    if not grading:
        grading = GradingScale.objects.filter(
            school=school,
            term__isnull=True,
            min_score__lte=average_score,
            max_score__gte=average_score,
        ).first()

    overall_grade = grading.grade if grading else "-"

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
    if request.user.role != 'student':
        messages.error(request, "Only students can view this page.")
        return redirect('accounts:student_dashboard')

    student = request.user
    setting = SchoolSetting.objects.filter(school=student.school).first()
    attendance_mode = setting.attendance_mode if setting else 'subject'

    years = AcademicYear.objects.filter(school=student.school).order_by('-name')
    terms = Term.objects.filter(school=student.school).order_by('-start_date')

    selected_year_id = request.GET.get('year')
    selected_term_id = request.GET.get('term')

    if selected_year_id:
        terms = terms.filter(academic_year_id=selected_year_id)

    if selected_term_id:
        active_term = Term.objects.filter(id=selected_term_id, school=student.school).first()
    elif selected_year_id:
        active_term = Term.objects.filter(academic_year_id=selected_year_id, school=student.school, is_active=True).first()
        if not active_term:
            active_term = Term.objects.filter(academic_year_id=selected_year_id, school=student.school).order_by('-start_date').first()
    else:
        active_term = Term.objects.filter(school=student.school, is_active=True).first()

    # ===== WEEK LOGIC - ONLY HERE, NOWHERE ELSE =====
    if active_term:
        all_weeks = get_weeks_for_term(active_term)
        total_weeks = len(all_weeks) if all_weeks else 1

        # FIRST OPEN = ACTIVE WEEK
        try:
            raw = request.GET.get('s_week')
            if raw is None or raw == '':
                raise ValueError
            week_offset = int(raw)
        except:
            today = timezone.now().date()
            active_w = get_active_week(active_term.start_date, today, active_term.end_date)
            week_offset = active_w - 1

        # clamp
        week_offset = max(0, min(week_offset, total_weeks - 1))
        target_monday, target_friday = all_weeks[week_offset]
        active_week = week_offset + 1
        s_prev_week = max(0, week_offset - 1)
        s_next_week = min(total_weeks - 1, week_offset + 1)
    else:
        today = timezone.now().date()
        target_monday = today - timedelta(days=today.weekday())
        target_friday = target_monday + timedelta(days=4)
        active_week = 1
        total_weeks = 1
        week_offset = 0
        s_prev_week = 0
        s_next_week = 0

    # ... keep your attendance_mode code same as you have ...
    if attendance_mode == 'class_teacher':
        qs = AttendanceRecord.objects.filter(
            student=student,
            school=student.school,
            session__school=student.school,
            session__date__gte=target_monday,
            session__date__lte=target_friday
        )
        if selected_year_id:
            term_ids = Term.objects.filter(academic_year_id=selected_year_id, school=student.school).values_list('id', flat=True)
            qs = qs.filter(session__term_id__in=term_ids)
        if selected_term_id:
            qs = qs.filter(session__term_id=selected_term_id)

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
        absent = total - present
        by_subject = []

    else:
        qs = AttendanceRecord.objects.filter(
            student=student,
            school=student.school,
            session__school=student.school,
            session__date__gte=target_monday,
            session__date__lte=target_friday,
        ).select_related("session__subject", "session__teacher", "session__term")

        if selected_year_id:
            term_ids = Term.objects.filter(academic_year_id=selected_year_id, school=student.school).values_list('id', flat=True)
            qs = qs.filter(session__term_id__in=term_ids)
        if selected_term_id:
            qs = qs.filter(session__term_id=selected_term_id)

        records = qs.order_by('-session__date')

        subject_data = defaultdict(lambda: {'name': '', 'total': 0, 'present': 0, 'absent': 0})
        for record in records:
            if record.session and record.session.subject:
                subj_name = record.session.subject.name
                subject_data[subj_name]['name'] = subj_name
                subject_data[subj_name]['total'] += 1
                if record.status == 'P':
                    subject_data[subj_name]['present'] += 1
                else:
                    subject_data[subj_name]['absent'] += 1

        by_subject = []
        for subject in subject_data.values():
            subject['percentage'] = round((subject['present'] / subject['total']) * 100, 1) if subject['total'] else 0
            by_subject.append(subject)

        by_date_overall = defaultdict(list)
        for r in records:
            by_date_overall[r.session.date].append(r.status)

        total = len(by_date_overall)
        present = sum(1 for s in by_date_overall.values() if 'P' in s)
        absent = total - present

    context = {
        "records": records,
        "total": total,
        "present": present,
        "absent": absent,
        "late": 0,
        "s_week_start": target_monday,
        "s_week_end": target_friday,
        "s_prev_week": s_prev_week,
        "s_next_week": s_next_week,
        "s_active_week": active_week,
        "s_week": week_offset,
        "total_weeks": total_weeks,
        "attendance_mode": attendance_mode,
        "active_term": active_term,
        "by_subject": by_subject,
        "years": years,
        "terms": terms,
        "selected_year": selected_year_id or "",
        "selected_term": selected_term_id or "",
        "school": student.school,
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
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_review_results:
                messages.error(request, "Admin has not allowed you to review results")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif request.user.role not in ["admin", "proprietor", "proprietress"]:
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

            # NEW GRADING SYSTEM
            r.total = float(r.total_score or 0)
            r.review_ca = float(r.class_score or 0)
            r.review_exam = float(r.exam_converted_score or 0)

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
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_review_results:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif request.user.role not in ['admin', 'proprietor', 'proprietress']:
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
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_review_results:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif request.user.role not in ["admin", "proprietor", "proprietress"]: 
        return redirect("accounts:dashboard")

    school = request.user.school

    result = get_object_or_404(Result, pk=pk, status="submitted", school=school)

    if request.method == "POST":
        result.status = "returned"
        result.returned_reason = request.POST.get("reason", "")
        result.save()
        messages.warning(request, f"Result returned to teacher.")

    return redirect("accounts:admin_review_results")


def test_view(request):
    return HttpResponse("Test page works")


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
    if request.user.role not in ['admin', 'proprietor',
    'proprietress', 'headmaster', 'headmistress']:
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        school = request.user.school
        ...

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
    if request.user.role not in [  'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress', "teacher"]:
        return redirect("accounts:dashboard")

    school = request.user.school
    student = get_object_or_404(User, id=student_id, role="student", school=school)

    # ✅ TEACHER ISOLATION - NEW!
    if request.user.role == "teacher":
        # Teacher must teach this student's class
        has_class = TeacherSubjectClass.objects.filter(
            teacher=request.user,
            school_class=student.school_class,
            school_class__school=school,
        ).exists()

        if not has_class:
            messages.error(
                request, "You can only view grades for students in classes you teach!"
            )
            return redirect("accounts:dashboard")

    active_term = Term.objects.filter(is_active=True, school=school).first()

    if not active_term:
        messages.error(request, "No active term set.")
        return render(
            request,
            "accounts/student_grades.html",
            {
                "student": student,
                "student_results": [],
                "active_term": None,
            },
        )

    term = active_term
    year = active_term.academic_year

    student_results = (
        Result.objects.filter(
            school=school,
            student=student,
            term=term,
            academic_year=year,
            status= "published",
        )
        .select_related("subject")
        .order_by("subject__name")
    )

    # ... rest same
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
        if valid_count:
            overall_average = total_percentage / valid_count

        grading = GradingScale.objects.filter(
            school=school,
            term=term,
            min_score__lte=overall_average,
            max_score__gte=overall_average,
        ).first()

        if not grading:
            grading = GradingScale.objects.filter(
                school=school,
                term__isnull=True,
                min_score__lte=overall_average,
                max_score__gte=overall_average,
            ).first()

        if grading:
            overall_grade = grading.grade
            overall_remark = grading.remark
        else:
            overall_grade = "-"
            overall_remark = "-"

    return render(
        request,
        "accounts/student_grades.html",
        {
            "student": student,
            "student_results": student_results,
            "term": term,
            "academic_year": year,
            "active_term": active_term,
            "overall_average": overall_average,
            "overall_grade": overall_grade,
            "overall_remark": overall_remark,
            "ca_total": active_term.ca_total,
            "exam_total": active_term.exam_total,
        },
    )


@login_required
def edit_student_grade(request, assignment_id):
    school = request.user.school
    
    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
    
    assignment = get_object_or_404(StudentSubjectClass, id=assignment_id, school=school)
    
    # Role check
    if request.user.role not in [  'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress', 'teacher'] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')
    
    # ✅ TEACHER ISOLATION - NEW
    if request.user.role == 'teacher':
        is_his = TeacherSubjectClass.objects.filter(
            teacher=request.user,
            subject=assignment.subject,
            school_class=assignment.school_class,
            school_class__school=school
        ).exists()
        if not is_his:
            messages.error(request, "You can only edit grades for subjects you teach!")
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ] and not request.user.is_superuser:
        messages.error(
            request,
            "Administrator, Proprietor, or Headmaster access required"
        )
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
    if request.user.role not in [ 'admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress'] and not request.user.is_superuser:
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
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'] and not request.user.is_superuser:
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
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'] and not request.user.is_superuser:
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
        r.total = float(r.total_score or 0)
        r.review_ca = float(r.class_score or 0)
        r.review_exam = float(r.exam_converted_score or 0)

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
    if (
        request.user.role not in [
            "admin",
            "proprietor",
            "proprietress",
            "headmaster",
            "headmistress",
        ]
        and not request.user.is_superuser
    ):
        return redirect("accounts:dashboard")

    school = request.user.school

    school_class = get_object_or_404(SchoolClass, id=class_id, school=school)

    active_term = Term.objects.filter(is_active=True, school=school).first()

    if not active_term:
        return HttpResponse(
            "No active term set. Ask admin to set one in Term Settings.", status=400
        )

    term = int(request.GET.get("term", active_term.term_number))

    year = int(request.GET.get("year", active_term.academic_year.id))

    results = Result.objects.filter(
        school=school,
        student__school_class=school_class,
        term__term_number=term,
        academic_year_id=year,
        status="published",
    ).select_related("student", "subject", "term", "term__academic_year")

    student_data = {}
    subjects = set()

    for result in results:

        student = result.student
        subject = result.subject

        student_name = student.get_full_name()
        student_number = student.student_number

        if student_name not in student_data:
            student_data[student_name] = {
                "student_number": student_number,
                "subjects": {},
                "total": 0,
            }

        # Use the current grading system
        ca_total = float(result.class_score or 0)
        exam = float(result.exam_converted_score or 0)
        total = float(result.total_score or 0)

        subject_code = subject.code or subject.name

        subjects.add(subject_code)

        student_data[student_name]["subjects"][subject_code] = {
            "ca": ca_total,
            "exam": exam,
            "total": total,
        }

        student_data[student_name]["total"] += total

    # Rank students by total score
    sorted_students = sorted(
        student_data.items(), key=lambda x: x[1]["total"], reverse=True
    )

    for position, (name, data) in enumerate(sorted_students, start=1):
        data["position"] = position

    subjects = sorted(list(subjects))

    return render(
        request,
        "accounts/print_class_report.html",
        {
            "school_class": school_class,
            "term": term,
            "year": year,
            "active_term": active_term,
            "students": sorted_students,
            "subjects": subjects,
        },
    )


def get_position_suffix(pos):
    if 11 <= pos % 100 <= 13:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(pos % 10, 'th')


@login_required
def print_student_report(request, student_id):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
] and not request.user.is_superuser:
        return redirect('accounts:dashboard')

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
        get_object_or_404(SchoolClass, id=class_id, school=school)
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

            student_total += float(r.total_score or 0) 
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
            ca_total = float(r.class_score or 0)
            exam = float(r.exam_converted_score or 0)
            total = float(r.total_score or 0)
            grand_total += total
            grading = get_grading_scale(
                school,
                term_obj,
                total
            )

            grade = grading.grade if grading else "-"
            remark = grading.remark if grading else "-"
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

        grading = get_grading_scale(
            school,
            term_obj,
            average
        )
        if grading:
            overall_grade = grading.grade
            overall_remark = grading.remark
        else:
            overall_grade = "-"
            overall_remark = "-"

        active_term_for_remark = Term.objects.filter(
            is_active=True, school=school
        ).first()
        remark_obj = StudentRemark.objects.filter(
            school=school,
            student=student,
            term=active_term_for_remark,
        ).first()

        if not remark_obj:
            remark_obj = (
                StudentRemark.objects.filter(school=school, student=student)
                .order_by("-id")
                .first()
            )

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
                "headmaster_remark": (
                    remark_obj.headmaster_remark if remark_obj else ""
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
    if (
        not request.user.is_class_teacher
        and request.user.role not in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
]
        and not request.user.is_superuser
    ):
        return HttpResponse("Only class teachers can access this", status=403)

    school = request.user.school

    # If headmaster/admin - allow to choose class via ?class_id=
    class_id = request.GET.get("class_id")
    if request.user.role in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"] or request.user.is_superuser:
        if class_id:
            class_obj = get_object_or_404(SchoolClass, id=class_id, school=school)
        else:
            # Show first class or list
            class_obj = SchoolClass.objects.filter(school=school).first()
            if not class_obj:
                return HttpResponse("No class found", status=404)
    else:
        # Class teacher - only his class
        class_obj = request.user.class_teacher_of
        if not class_obj or class_obj.school_id != school.id:
            return HttpResponse("You are not assigned as class teacher", status=403)

    term = Term.objects.filter(is_active=True, school=school).first()
    if not term:
        return HttpResponse("No active term found", status=403)

    students = User.objects.filter(
        role="student", school_class=class_obj, school=school
    ).order_by("last_name", "first_name")

    remarks = []
    for student in students:
        remark, created = StudentRemark.objects.get_or_create(
            student=student, term=term, school=school, defaults={"school": school}
        )
        remarks.append({"student": student, "remark": remark})

    setting = SchoolSetting.objects.filter(school=school).first()
    attendance_mode = setting.attendance_mode if setting else "subject"

    # For headmaster to switch classes
    all_classes = (
        SchoolClass.objects.filter(school=school)
        if request.user.role in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
]
        else None
    )

    return render(
        request,
        "accounts/class_remarks.html",
        {
            "remarks": remarks,
            "class_obj": class_obj,
            "all_classes": all_classes,
            "attendance_mode": attendance_mode,
            "term": term,
        },
    )


@login_required
def save_student_remark(request, student_id):
    if request.method != "POST":
        return HttpResponse("Invalid request", status=400)

    school = request.user.school
    student = get_object_or_404(User, id=student_id, school=school)
    term = Term.objects.filter(is_active=True, school=school).first()

    if not term:
        return HttpResponse("No active term found", status=403)

    # FIXED PERMISSION - Allow Class Teacher OR Head Teacher OR Admin
    is_owner_class_teacher = (
        request.user.is_class_teacher
        and student.school_class == request.user.class_teacher_of
    )
    is_head_or_admin = (
        request.user.role in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
]
        or request.user.is_superuser
    )

    if not (is_owner_class_teacher or is_head_or_admin):
        return HttpResponse(
            "You can only edit remarks for your own class or as head teacher",
            status=403,
        )

    remark, created = StudentRemark.objects.get_or_create(
        student=student, term=term, school=school, defaults={"school": school}
    )

    # Class teacher can save class remark
    if is_owner_class_teacher or request.user.role in [
    "admin",
    "proprietor",
    "proprietress",
    "headmaster",
    "headmistress",
]:
        new_class_remark = request.POST.get("class_teacher_remark")
        if new_class_remark is not None:
            remark.class_teacher_remark = new_class_remark.strip()

    # Head teacher / Admin can save head remark
    if is_head_or_admin:
        new_head_remark = request.POST.get("headmaster_remark")
        if new_head_remark is not None:
            remark.headmaster_remark = new_head_remark.strip()

    remark.save()
    return HttpResponse("Remarks saved successfully")


@login_required
def add_subject(request):
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
        return redirect("accounts:dashboard")

    # If headmaster, check permission
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_subject:
                messages.error(request, "Admin has not allowed you")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")

    if request.method == "POST":
        name = request.POST.get("name")
        code = request.POST.get("code")

        existing_subject = Subject.objects.filter(
            school=request.user.school, name__iexact=name
        ).first()

        if existing_subject:
            messages.warning(request, "Subject already exists in your school.")
            return redirect("accounts:add_subject")

        Subject.objects.create(name=name, code=code, school=request.user.school)
        messages.success(request, "Subject added successfully")
        return redirect("accounts:subject_list")

    return render(request, "accounts/add_subject.html", {"school": request.user.school})


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
            return redirect("accounts:term_settings")

        term = get_object_or_404(Term, id=term_id, school=school)

        Term.objects.filter(is_active=True, school=school).exclude(pk=term.pk).update(
            is_active=False
        )

        try:
            term.days_opened = calculate_school_days(
                term.start_date, term.end_date, school
            )
        except:
            pass

        term.is_active = True
        term.save()

        if ca and exam:
            TermSetting.objects.update_or_create(
                term=term, defaults={"ca_total": ca, "exam_total": exam}
            )

        messages.success(
            request,
            f"Active term set to: {term.academic_year} - Term {term.term_number} (Extended to {term.end_date})",
        )

    return redirect("accounts:term_settings")


@login_required
def term_settings(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')

    active_term = Term.objects.filter(
        is_active=True,
        school=request.user.school
    ).first()

    if request.method == 'POST':
        form = TermSettingForm(request.POST, school=request.user.school)

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

                # Make this academic year the active academic year
                AcademicYear.objects.filter(
                    school=request.user.school,
                    is_active=True
                ).update(is_active=False)

                term.academic_year.is_active = True
                term.academic_year.save()

            term.save()

            messages.success(request, "Term saved successfully")
            return redirect('accounts:term_settings')

    else:
        form = TermSettingForm(school=request.user.school)

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
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')
    school = request.user.school
    term = get_object_or_404(Term, pk=pk, school=school)

    if request.method == "POST":
        form = TermForm(request.POST, instance=term)
        if form.is_valid():
            if form.cleaned_data["is_active"]:
                Term.objects.filter(is_active=True, school=school).exclude(
                    pk=pk
                ).update(is_active=False)

            edited_term = form.save(commit=False)
            edited_term.school = school
            edited_term.days_opened = calculate_school_days(
                edited_term.start_date, edited_term.end_date, school
            )
            edited_term.save()

            messages.success(request, "Term updated")
            return redirect("accounts:term_settings")
        else:
            messages.error(request, "Error: " + str(form.errors))
    else:
        form = TermForm(instance=term)

    return render(request, "accounts/edit_term.html", {"form": form, "term": term})


@login_required
def delete_term(request, pk):
    if request.user.role not in ['admin','headmaster'] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')
    
    if request.method != "POST":  # ✅ Prevent GET delete via link!
        return HttpResponse("Use POST", status=405)
        
    school = request.user.school
    term = get_object_or_404(Term, pk=pk, school=school)
    term.delete()
    messages.success(request, "Term deleted")
    return redirect('accounts:term_settings')

@login_required
def add_class(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')
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


@role_required(["admin", "hod", "proprietor", "proprietress", "headmaster", "headmistress"])
def timetable_manager_create_timetable(request):
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
        messages.error(request, "Permission denied")
        return redirect("accounts:dashboard")

    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_create_timetable:
                messages.error(request, "Not allowed by admin")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")

    school = request.user.school
    # ... rest of your code

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
                active_term = Term.objects.filter(
                    school=school,
                    is_active=True
                ).select_related('academic_year').first()

                if not active_term:
                    messages.error(
                        request,
                        "Please set an active term before creating a timetable."
                    )
                    return redirect("accounts:timetable_create")

                Timetable.objects.create(
                    school=school,
                    teacher_id=teacher_id,
                    subject_id=subject_id,
                    school_class_id=class_id,
                    term=active_term,
                    academic_year=active_term.academic_year,
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
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')

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
        break_times.append(f"{br.start_time.strftime('%H:%M')} - {br.end_time.strftime('%H:%M')}")

        # FIXED - moved outside loop + fixed filter
    all_terms = Term.objects.filter(school=school).order_by('-academic_year__start_date', 'term_number')

    # For copy - need active_term
    active_term = Term.objects.filter(school=school, is_active=True).first() or Term.objects.filter(school=school).first()
    selected_year = active_term.academic_year.id if active_term and active_term.academic_year else None

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
                'all_terms': all_terms,
                'active_term': active_term,
                'selected_year': selected_year,
            })


@login_required
def copy_timetable_to_term(request):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect("accounts:dashboard")

    school = request.user.school
    from_term = request.GET.get("from_term")
    to_term = request.GET.get("to_term")

    if not from_term or not to_term:
        messages.error(request, "Select target term")
        return redirect("/timetable/manage/")

    from_term_obj = get_object_or_404(Term, id=from_term, school=school)
    to_term_obj = get_object_or_404(Term, id=to_term, school=school)
    # BULK - copy ALL classes, ALL stages for that term
    old_qs = Timetable.objects.filter(school=school, term=from_term_obj)

    count = 0
    for tt in old_qs:
        if not Timetable.objects.filter(
            school=school,
            school_class=tt.school_class,
            academic_year=to_term_obj.academic_year,
            term=to_term_obj,
            weekday=tt.weekday,
            start_time=tt.start_time,
        ).exists():
            Timetable.objects.create(
                school=school,
                school_class=tt.school_class,
                academic_year=to_term_obj.academic_year,
                term=to_term_obj,
                weekday=tt.weekday,
                start_time=tt.start_time,
                end_time=tt.end_time,
                subject=tt.subject,
                teacher=tt.teacher,
            )
            count += 1

    messages.success(
        request,
        f"Copied WHOLE TERM: {count} periods from {from_term_obj} to {to_term_obj} - All classes!",
    )
    return redirect(f"/timetable/manage/")


@role_required(["admin","proprietor","proprietress","headmaster","headmistress"])  # ✅ Same as other functions
def timetable_manager_delete(request, timetable_id):
    if (
        request.user.role not in ["admin","proprietor","proprietress","headmaster","headmistress",]
        and not request.user.is_superuser
    ):
        messages.error(request, "Permission denied")
        return redirect("accounts:dashboard")

    school = request.user.school
    timetable = get_object_or_404(
        Timetable, id=timetable_id, school_class__school=school
    )
    timetable.delete()
    messages.success(request, "Timetable entry deleted")
    return redirect("accounts:timetable_create")


@login_required
def student_timetable(request):
    if request.user.role != "student":
        return redirect("accounts:student_dashboard")

    student = request.user
    school = student.school

    if not school:
        messages.error(request, "No school assigned.")
        return redirect("accounts:student_dashboard")

    filter_data = get_term_year_filter(request, school)
    years = filter_data["years"]

    today_date = timezone.localtime(timezone.now()).date()

    # =========================================================
    # YEAR / TERM SELECTION
    # =========================================================

    selected_year = request.GET.get("year")
    selected_term = request.GET.get("term")

    # ---------------------------------------------------------
    # DEFAULT TO CURRENT ACTIVE TERM
    # ---------------------------------------------------------

    if not selected_year and not selected_term:

        active_term_default = (
            Term.objects.filter(school=school, is_active=True)
            .select_related("academic_year")
            .first()
        )

        if active_term_default:

            selected_year = active_term_default.academic_year_id
            selected_term = active_term_default.term_number

        else:

            # Fallback: find the term containing today's date
            active_term_default = (
                Term.objects.filter(
                    school=school, start_date__lte=today_date, end_date__gte=today_date
                )
                .select_related("academic_year")
                .first()
            )

            if active_term_default:

                selected_year = active_term_default.academic_year_id
                selected_term = active_term_default.term_number

    # Convert to strings for template comparisons
    sy = str(selected_year) if selected_year else ""
    st = str(selected_term) if selected_term else ""

    # =========================================================
    # IF YEAR OR TERM IS STILL MISSING
    # =========================================================

    if not sy or not st:

        return render(
            request,
            "accounts/student_timetable.html",
            {
                **filter_data,
                "years": years,
                "selected_year": sy,
                "selected_term": st,
                "timetable_by_day": [],
                "today": today_date,
                "need_both": True,
                "timetable_total_weeks": 1,
                "timetable_active_week": 1,
                "timetable_today_week": 1,
                "time_slots": [],
                "break_slots": [],
                "break_times": [],
            },
        )

    # =========================================================
    # FIND SELECTED TERM
    # =========================================================

    active_term = (
        Term.objects.filter(school=school, academic_year_id=sy, term_number=st)
        .select_related("academic_year")
        .first()
    )

    if not active_term:

        return render(
            request,
            "accounts/student_timetable.html",
            {
                **filter_data,
                "years": years,
                "selected_year": sy,
                "selected_term": st,
                "timetable_by_day": [],
                "today": today_date,
                "no_timetable": True,
                "timetable_total_weeks": 1,
                "timetable_active_week": 1,
                "timetable_today_week": 1,
                "time_slots": [],
                "break_slots": [],
                "break_times": [],
            },
        )

    # =========================================================
    # TERM DATES
    # =========================================================

    term_start = active_term.start_date
    term_end = active_term.end_date

    # =========================================================
    # CURRENT WEEK
    # =========================================================

    current_week_num = 1

    cur = term_start
    week_counter = 1

    while cur <= term_end:

        friday = cur + timedelta(days=(4 - cur.weekday()))

        week_end_calc = min(friday, term_end)

        if cur <= today_date <= week_end_calc:

            current_week_num = week_counter
            break

        cur = friday + timedelta(days=3)
        week_counter += 1

    # =========================================================
    # ACTIVE WEEK
    # =========================================================

    week_param = request.GET.get("t_week") or request.GET.get("week")

    try:

        active_week = int(week_param) if week_param else current_week_num

    except (TypeError, ValueError):

        active_week = current_week_num

    # =========================================================
    # TOTAL WEEKS
    # =========================================================

    total_weeks = 0
    cur = term_start

    while cur <= term_end:

        total_weeks += 1

        friday = cur + timedelta(days=(4 - cur.weekday()))

        cur = friday + timedelta(days=3)

    active_week = max(1, min(active_week, total_weeks or 1))

    # =========================================================
    # WEEK START / END
    # =========================================================

    week_start = term_start

    for _ in range(active_week - 1):

        friday = week_start + timedelta(days=(4 - week_start.weekday()))

        week_start = friday + timedelta(days=3)

    week_end = week_start + timedelta(days=(4 - week_start.weekday()))

    if week_end > term_end:
        week_end = term_end

    # =========================================================
    # TIMETABLE
    # =========================================================

    timetable_grid = {}

    if not student.school_class:

        timetable = Timetable.objects.none()
        breaks = Break.objects.none()

    else:

        timetable = (
            Timetable.objects.filter(
                school_class=student.school_class, school_class__school=school
            )
            .select_related("subject", "teacher")
            .order_by("weekday", "period")
        )

        breaks = Break.objects.filter(
            school=school, stage=student.school_class.stage
        ).order_by("start_time")

    # =========================================================
    # TIMETABLE GRID
    # =========================================================

    for entry in timetable:

        slot = (
            f"{entry.start_time.strftime('%H:%M')} - "
            f"{entry.end_time.strftime('%H:%M')}"
        )

        timetable_grid.setdefault(entry.weekday, {})

        timetable_grid[entry.weekday][slot] = entry

    # =========================================================
    # TIME SLOTS
    # =========================================================

    time_slots = set()

    for entry in timetable:

        if entry.start_time and entry.end_time:

            time_slots.add(
                f"{entry.start_time.strftime('%H:%M')} - "
                f"{entry.end_time.strftime('%H:%M')}"
            )

    # =========================================================
    # BREAKS
    # =========================================================

    break_slots = []

    for br in breaks:

        label = (
            f"{br.start_time.strftime('%H:%M')} - " f"{br.end_time.strftime('%H:%M')}"
        )

        time_slots.add(label)

        break_slots.append(
            {
                "id": br.id,
                "name": br.name,
                "start": br.start_time,
                "end": br.end_time,
                "label": label,
            }
        )

    time_slots = sorted(time_slots)

    break_times = [br["label"] for br in break_slots]

    # =========================================================
    # DAYS
    # =========================================================

    timetable_by_day = []

    cur_date = week_start

    while cur_date <= week_end:

        if cur_date.weekday() < 5:

            event = AcademicCalendar.objects.filter(
                school=school, start_date__lte=cur_date, end_date__gte=cur_date
            ).first()

            entries = [e for e in timetable if e.weekday == cur_date.weekday()]

            if event and event.affects_timetable:

                entries = []

            else:

                for e in entries:

                    is_break = False

                    if getattr(e, "is_break", False):

                        is_break = True

                    elif not e.subject:

                        is_break = True

                    elif getattr(getattr(e, "subject", None), "name", "").upper() in [
                        "BREAK",
                        "RECESS",
                        "LUNCH",
                        "BREAK TIME",
                    ]:

                        is_break = True

                    e.is_break_entry = is_break

            timetable_by_day.append(
                {
                    "num": cur_date.weekday(),
                    "name": cur_date.strftime("%A"),
                    "date": cur_date,
                    "entries": entries,
                    "event": event,
                }
            )

        cur_date += timedelta(days=1)

    # =========================================================
    # FINAL CONTEXT
    # =========================================================

    return render(
        request,
        "accounts/student_timetable.html",
        {
            **filter_data,
            "years": years,
            "selected_year": sy,
            "selected_term": st,
            "timetable_by_day": timetable_by_day,
            "today": today_date,
            "today_weekday": (
                today_date.weekday() if term_start <= today_date <= term_end else -1
            ),
            "timetable_active_week": active_week,
            "timetable_prev_week": (active_week - 1 if active_week > 1 else None),
            "timetable_next_week": (
                active_week + 1 if active_week < total_weeks else None
            ),
            "timetable_total_weeks": total_weeks,
            "current_week_start": week_start,
            "current_week_end": week_end,
            "active_term": active_term,
            "timetable_today_week": current_week_num,
            "time_slots": time_slots,
            "break_slots": break_slots,
            "break_times": break_times,
            "timetable_grid": timetable_grid,
        },
    )


@role_required(['hod', 'headmaster', 'admin'])
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

        teacher = get_object_or_404(User, id=teacher_id, school=school, role='teacher')
        subject = get_object_or_404(Subject, id=subject_id, school=school)

        timetable.teacher = teacher
        timetable.subject = subject
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


@role_required(['hod','headmaster','admin'])
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
        br.start_time = start_time
        br.end_time = end_time

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


@role_required(['admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress'])
@login_required
def add_calendar_event(request):
    if request.user.role not in ['admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress'] and not request.user.is_superuser:
        messages.error(request, "Permission denied")
        return redirect('accounts:dashboard')
    
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

@role_required(['admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress'])
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

@role_required(['admin', 'proprietor', 'proprietress', 'headmaster', 'headmistress'])  # ✅ ADD THIS
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
def add_headmaster(request):
    if request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect("accounts:home")

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect("accounts:home")

    if request.method == "POST":
        form = HeadmasterForm(request.POST, request.FILES)

        if form.is_valid():
            headmaster = form.save(commit=False)
            headmaster.role = form.cleaned_data.get("role")
            headmaster.school = request.user.school
            headmaster.is_password_changed = False
            headmaster.is_active = True

            base_username = (
                f"{headmaster.first_name.lower()}.{headmaster.last_name.lower()}"
            )
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1
            headmaster.username = username

            password = form.cleaned_data.get("password") or "Staff2026"
            headmaster.set_password(password)
            headmaster.save()
            form.save_m2m()

            request.session["credentials"] = {
                "title": "Headmaster Login Credentials",
                "username": headmaster.username,
                "staff_id": headmaster.staff_id,
                "password": password,
            }

            return redirect("accounts:manage_headmaster_grid")

        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = HeadmasterForm()

    return render(request, "accounts/add_headmaster.html", {"form": form})

@login_required
def manage_headmaster_grid(request):
    if request.user.role not in ['admin', 'proprietor', 'proprietress']:
        return redirect('accounts:home')
    credentials = request.session.pop("credentials", None)
    
    search_query = request.GET.get('q', '')
    
    headmasters = User.objects.filter(
        role__in=['headmaster', 'headmistress'],
        is_active=True,
        school=request.user.school
    )
    
    if search_query:
        headmasters = headmasters.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(username__icontains=search_query) |
            Q(staff_id__icontains=search_query)   
        )
    
    headmasters = headmasters.order_by('first_name')
    paginator = Paginator(headmasters, 10)
    page = request.GET.get('page')
    headmasters = paginator.get_page(page)
    
    return render(request, "accounts/manage_headmaster_grid.html", {
        "headmasters": headmasters,
        "search_query": search_query,
            'credentials': credentials
    })

@login_required
def headmaster_my_profile(request):
    if request.user.role not in ["headmaster", "headmistress"]:
        messages.error(request, "You are not authorized to view this page.")
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school:
        messages.error(request, "No school is assigned to your account.")
        return redirect("accounts:dashboard")

    headmaster = User.objects.get(
        id=request.user.id,
        school=school,
        role=request.user.role,
    )

    return render(
        request,
        "accounts/headmaster_my_profile.html",
        {
            "headmaster": headmaster,
            "school": school,
        },
    )

@login_required
def view_headmaster(request, headmaster_id):
    if request.user.role not in ['admin', 'proprietor', 'proprietress']:
        return redirect('accounts:home')
    
    headmaster = get_object_or_404(
        User,
        id=headmaster_id,
        role__in=['headmaster', 'headmistress'],
        school=request.user.school
    )
    return render(request, "accounts/view_headmaster.html", {"headmaster": headmaster})

@login_required
def edit_headmaster(request, headmaster_id):
    if request.user.role not in ['admin', 'proprietor', 'proprietress']:
        return redirect('accounts:home')

    headmaster = get_object_or_404(
    User,
    id=headmaster_id,
    role__in=['headmaster', 'headmistress'],
    school=request.user.school
)
    
    if request.method == 'POST':
        form = HeadmasterEditForm(request.POST, request.FILES, instance=headmaster)  
        if form.is_valid():
            form.save()
            messages.success(request, 'Headmaster updated successfully.')
            return redirect('accounts:view_headmaster', headmaster_id=headmaster.id)
    else:
        form = HeadmasterEditForm(instance=headmaster)  
    
    return render(request, "accounts/edit_headmaster.html", {
        "form": form,
        "headmaster": headmaster
    })


@login_required
def delete_headmaster(request, headmaster_id):
    if request.user.role not in ['admin', 'proprietor', 'proprietress']:
        return redirect('accounts:home')
    if request.method == "POST":
        User.objects.filter(
            id=headmaster_id, 
            role__in=['headmaster', 'headmistress'],
            school=request.user.school  # ✅ add this
        ).update(is_active=False)
        messages.success(request, "Head Teacher deactivated.")
    return redirect('accounts:manage_headmaster_grid')

@login_required
def deactivated_headmasters(request):
    if request.user.role not in ['admin', 'proprietor', 'proprietress']:
        return redirect('accounts:home')
        
    headmasters = User.objects.filter(
        role__in=['headmaster', 'headmistress'],
        is_active=False,  
        school=request.user.school
    ).order_by('-date_joined')
    return render(request, "accounts/deactivated_headmasters.html", {"headmasters": headmasters})

@login_required
def headmaster_profile(request):
    # If logged in user is headmaster, pass them as 'headmaster'
    context = {
        'headmaster': request.user
    }
    return render(request, 'accounts/headmaster_profile.html', context)


@login_required
def reactivate_headmaster(request, user_id):
    if request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect('accounts:home')

    headmaster = get_object_or_404(
        User,
        id=user_id,
        role__in=["headmaster", "headmistress"],
        school=request.user.school,
    )
    headmaster.is_active = True
    headmaster.save()
    messages.success(request, f"{headmaster.get_full_name()} has been reactivated.")
    return redirect('accounts:deactivated_headmasters')


@login_required(login_url="accounts:login")
def headmaster_dashboard(request):
    if request.user.role not in ["headmaster", "headmistress"]:
        return redirect("accounts:login")

    school = request.user.school

    if not school:
        return redirect("accounts:login")

    User = request.user.__class__

    # ============================
    # TOTAL ACCOUNTS
    # ============================

    total_students = User.objects.filter(school=school, role="student").count()

    total_teachers = User.objects.filter(school=school, role="teacher").count()

    total_parents = User.objects.filter(school=school, role="parent").count()

    total_accountants = User.objects.filter(school=school, role="accountant").count()

    if school.edition == "basic":
        total_users = User.objects.filter(
            school=school
        ).exclude(
            role__in=["parent", "accountant"]
        ).count()
    else:
        total_users = User.objects.filter(
            school=school
        ).count()

    # ============================
    # ACADEMICS
    # ============================

    total_subjects = Subject.objects.filter(school=school).count()

    total_classes = SchoolClass.objects.filter(school=school).count()

    # ============================
    # HEADMASTER PERMISSIONS
    # ============================

    # ============================
    # HEADMASTER PERMISSIONS
    # ============================

    owner_exists = User.objects.filter(
        school=school,
        role__in=["admin", "proprietor", "proprietress"]
    ).exists()

    if owner_exists:
        # Owner-controlled school:
        # Headmaster permissions start disabled.
        perms, created = HeadmasterPermission.objects.get_or_create(
            school=school
        )
    else:
        # Headmaster-only school:
        # Headmaster becomes the highest authority.
        perms, created = HeadmasterPermission.objects.get_or_create(
            school=school,
            defaults={
                "can_add_class": True,
                "can_add_subject": True,
                "can_assigned_teachers": True,
                "can_create_timetable": True,
                "can_manage_timetable": True,
                "can_calendar": True,

                "can_add_student": True,
                "can_add_parent": True,
                "can_add_teacher": True,
                "can_add_accountant": True,
                "can_add_headmaster": True,

                "can_attendance": True,
                "can_teacher_attendance": True,
                "can_review_results": True,
                "can_published_results": True,

                "can_fees": True,
                "can_edit_student_fees": True,
                "can_expenses": True,
                "can_fee_monitoring": True,

                "can_create_announcements": True,
                "can_notifications": True,

                "can_view_reports": True,
                "can_headmaster_remarks": True,

                "can_school_settings": True,
                "can_user_management": True,
                "can_system_settings": True,
            },
        )
    # ============================
    # ACTIVE ACADEMIC YEAR / TERM
    # ============================

    academic_year = AcademicYear.objects.filter(school=school, is_active=True).first()

    current_term = Term.objects.filter(school=school, is_active=True).first()

    context = {
        "school": school,
        "current_term": current_term,
        "academic_year": academic_year,
        # Accounts
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_parents": total_parents,
        "total_accountants": total_accountants,
        "total_users": total_users,
        # Academics
        "total_subjects": total_subjects,
        "total_classes": total_classes,
        # Permissions
        "perms": perms,
        "today": timezone.localdate(),
    }

    return render(request, "accounts/headmaster_dashboard.html", context)


@login_required
def manage_headmaster_permissions(request, school_id):
    # only admin can do this
    if request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect('accounts:dashboard')

    school = get_object_or_404(School, id=school_id)
    perm, created = HeadmasterPermission.objects.get_or_create(school=school)

    if request.method == 'POST':
        # update all checkboxes
        perm.can_add_class = 'can_add_class' in request.POST
        perm.can_add_subject = 'can_add_subject' in request.POST
        perm.can_assigned_teachers = 'can_assigned_teachers' in request.POST
        perm.can_create_timetable = 'can_create_timetable' in request.POST
        perm.can_manage_timetable = 'can_manage_timetable' in request.POST
        perm.can_calendar = 'can_calendar' in request.POST

        perm.can_add_student = 'can_add_student' in request.POST
        perm.can_add_parent = 'can_add_parent' in request.POST
        perm.can_add_teacher = 'can_add_teacher' in request.POST
        perm.can_add_accountant = 'can_add_accountant' in request.POST
        perm.can_add_headmaster = 'can_add_headmaster' in request.POST

        perm.can_attendance = 'can_attendance' in request.POST
        perm.can_teacher_attendance = 'can_teacher_attendance' in request.POST
        perm.can_review_results = 'can_review_results' in request.POST
        perm.can_published_results = 'can_published_results' in request.POST

        perm.can_fees = 'can_fees' in request.POST
        perm.can_edit_student_fees = 'can_edit_student_fees' in request.POST
        perm.can_expenses = 'can_expenses' in request.POST
        perm.can_fee_monitoring = 'can_fee_monitoring' in request.POST

        perm.can_create_announcements = 'can_create_announcements' in request.POST
        perm.can_notifications = 'can_notifications' in request.POST

        perm.can_view_reports = 'can_view_reports' in request.POST
        perm.can_headmaster_remarks = 'can_headmaster_remarks' in request.POST

        perm.can_school_settings = 'can_school_settings' in request.POST
        perm.can_user_management = 'can_user_management' in request.POST
        perm.can_system_settings = 'can_system_settings' in request.POST

        perm.save()
        messages.success(request, f"Permissions saved for {school.name}")
        return redirect('accounts:manage_headmaster_grid')

    return render(request, 'accounts/manage_headmaster_permissions.html', {
        'school': school,
        'perm': perm
    })


@login_required
def add_accountant(request):
    credentials = None

    if not request.user.school:
        messages.error(request, "You are not assigned to a school.")
        return redirect("accounts:home")

    # --- FIXED PERMISSION - CORRECT ---
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant:  # <-- was can_add_student before!
                messages.error(request, "Admin has not allowed you to add accountants")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
    elif request.user.role not in ["admin", "proprietor", "proprietress"]:
        return redirect("accounts:home")

    # ... keep rest of your code ...
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:home')

    # If headmaster, check permission too
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:home')
    
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant: # or can_view_accountant permission
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")

    accountant = get_object_or_404(User, id=accountant_id, role='accountant', school=request.user.school)
    return render(request, "accounts/view_accountant.html", {"accountant": accountant})


@login_required
def edit_accountant(request, accountant_id):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:home')
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
        
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
    context = {
        'accountant': request.user
    }
    return render(request, 'accounts/accountant_profile.html', context)


@login_required
def delete_accountant(request, accountant_id):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:home')
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
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
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:home')
    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")
        
    accountants = User.objects.filter(
        role='accountant', 
        is_active=False,  
        school=request.user.school
    ).order_by('-date_joined')
    return render(request, "accounts/deactivated_accountants.html", {"accountants": accountants})

@login_required  
def reactivate_accountant(request, user_id):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        return redirect('accounts:home')

    if request.user.role in ["headmaster", "headmistress"]:
        try:
            hp = request.user.school.headmaster_permissions
            if not hp.can_add_accountant:
                messages.error(request, "Not allowed")
                return redirect("accounts:headmaster_dashboard")
        except:
            return redirect("accounts:headmaster_dashboard")

    accountant = get_object_or_404(User, id=user_id, role='accountant', school=request.user.school)
    accountant.is_active = True  
    accountant.save()
    messages.success(request, f"{accountant.get_full_name()} has been reactivated.")
    return redirect('accounts:deactivated_accountants')


@login_required
def accountant_dashboard(request):
    if request.user.role != "accountant":
        return redirect("accounts:home")

    school = request.user.school
    today = date.today()

    active_term = Term.objects.filter(school=school, is_active=True).first()
    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one.")
        return render(
            request,
            "accounts/accountant_dashboard.html",
            {
                "active_term": None,
                "total_collected": 0,
                "total_outstanding": 0,
                "total_expected": 0,
                "total_defaulters": 0,
            },
        )

    academic_year = active_term.academic_year

    # Base querysets - ISOLATED ✅
    fees_this_term = (
        StudentFee.objects.filter(
            school=school, term=active_term, academic_year=academic_year
        )
        .select_related("student", "student__school_class")
        .prefetch_related("dynamic_items")
    )

    payments_this_term = PaymentTransaction.objects.filter(
        student__school=school,
        term=active_term,
        academic_year=academic_year,
        is_voided=False,
    )

    # 1 & 2. Collected / Expected / Outstanding from REAL transactions
    total_collected = (
        payments_this_term.aggregate(total=Sum("total_amount"))["total"] or 0
    )
    total_expected = fees_this_term.aggregate(total=Sum("total_amount"))["total"] or 0

    student_paid_map = {}
    for txn in payments_this_term:
        student_paid_map[txn.student_id] = student_paid_map.get(
            txn.student_id, 0
        ) + float(txn.total_amount or 0)

    total_outstanding = 0
    for fee in fees_this_term:
        paid = student_paid_map.get(fee.student_id, 0)
        bal = float(fee.total_amount) - paid
        if bal > 0:
            total_outstanding += bal

    # 3. Today
    display_date = today
    today_collections = (
        payments_this_term.filter(created_at__date=today).aggregate(
            total=Sum("total_amount")
        )["total"]
        or 0
    )

    # 4. Expenses
    total_expenses = (
        Expense.objects.filter(
            school=school,
            expense_date__range=(active_term.start_date, active_term.end_date),
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    total_students = User.objects.filter(
        school=school, role="student", is_active=True
    ).count()

    # 6. Today's payments by class
    today_payments = (
        PaymentTransaction.objects.filter(
            student__school=school, created_at__date=today
        )
        .select_related("student", "student__school_class")
        .order_by("-created_at")
    )

    payments_by_class = {}
    for payment in today_payments:
        class_name = (
            payment.student.school_class.name
            if payment.student and payment.student.school_class
            else "No Class"
        )
        payments_by_class.setdefault(class_name, [])
        if len(payments_by_class[class_name]) < 5:
            payments_by_class[class_name].append(payment)

    # 7. Defaulters
    defaulter_counts = {}
    defaulter_counts_by_stage = {stage: 0 for stage in STAGE_GROUP_MAP.keys()}
    total_defaulters = 0
    for fee in fees_this_term:
        paid = student_paid_map.get(fee.student_id, 0)
        if float(fee.total_amount) - paid > 0:
            class_name = (
                fee.student.school_class.name
                if fee.student.school_class
                else "Unassigned"
            )
            defaulter_counts[class_name] = defaulter_counts.get(class_name, 0) + 1
            total_defaulters += 1
            stage = fee.student.school_class.stage if fee.student.school_class else None
            if stage in defaulter_counts_by_stage:
                defaulter_counts_by_stage[stage] += 1

    # 8. Fees + Canteen/Feeding by stage - FIXED
    fees_by_stage = {
        stage: {"expected": 0, "collected": 0, "outstanding": 0, "student_count": 0}
        for stage in STAGE_GROUP_MAP.keys()
    }
    canteen_by_stage = {
        stage: {"expected": 0, "collected": 0, "outstanding": 0}
        for stage in STAGE_GROUP_MAP.keys()
    }

    # Build real feeding collected from PaymentItem

    feeding_collected_by_student = defaultdict(Decimal)

    feeding_items = (
        PaymentItem.objects.filter(
            transaction__school=school,
            transaction__term=active_term,
            transaction__academic_year=academic_year,
            transaction__is_voided=False,
        )
        .filter(Q(fee_name__icontains="canteen") | Q(fee_name__icontains="feed"))
        .values("transaction__student_id")
        .annotate(total=Sum("amount"))
    )

    for row in feeding_items:
        feeding_collected_by_student[row["transaction__student_id"]] = row[
            "total"
        ] or Decimal("0")

    for fee in fees_this_term:
        stage_match = (
            fee.student.school_class.stage if fee.student.school_class else None
        )
        if not stage_match or stage_match not in fees_by_stage:
            continue

        paid = Decimal(str(student_paid_map.get(fee.student_id, 0)))
        total_amt = Decimal(str(fee.total_amount))
        bal = total_amt - paid

        fees_by_stage[stage_match]["expected"] += float(total_amt)
        fees_by_stage[stage_match]["collected"] += float(paid)
        fees_by_stage[stage_match]["outstanding"] += float(bal) if bal > 0 else 0
        fees_by_stage[stage_match]["student_count"] += 1

        # FEEDING
        canteen_expected = Decimal("0")
        for i in fee.dynamic_items.all():
            name = i.name.lower()
            if "canteen" in name or "feed" in name:
                canteen_expected += Decimal(str(i.amount_due))

        canteen_collected = feeding_collected_by_student.get(
            fee.student_id, Decimal("0")
        )

        canteen_by_stage[stage_match]["expected"] += float(canteen_expected)
        canteen_by_stage[stage_match]["collected"] += float(canteen_collected)
        canteen_by_stage[stage_match]["outstanding"] += float(
            canteen_expected - canteen_collected
        )

    canteen_totals = {
        "expected": sum(s["expected"] for s in canteen_by_stage.values()),
        "collected": sum(s["collected"] for s in canteen_by_stage.values()),
        "outstanding": sum(s["outstanding"] for s in canteen_by_stage.values()),
    }

    # 8b. Fee structure by stage - DYNAMIC
    fee_structure_by_stage = {}
    fee_structures = DynamicFeeStructure.objects.filter(
        term=active_term, academic_year=academic_year, school=school, is_published=True
    ).prefetch_related("items")
    for fs in fee_structures:
        breakdown = {item.name: float(item.amount) for item in fs.items.all()}
        breakdown["total"] = float(fs.total_amount)
        fee_structure_by_stage[fs.stage] = breakdown

    recent_expenses = Expense.objects.filter(school=school).order_by("-expense_date")[
        :5
    ]

    context = {
        "active_term": active_term,
        "current_term": f"Term {active_term.term_number}",
        "current_year": academic_year,
        "total_collected": total_collected,
        "total_outstanding": total_outstanding,
        "total_expected": total_expected,
        "today_collections": today_collections,
        "display_date": display_date,
        "total_expenses": total_expenses,
        "net_balance": float(total_collected) - float(total_expenses),
        "total_students": total_students,
        "collection_rate": (
            round((float(total_collected) / float(total_expected) * 100), 1)
            if total_expected > 0
            else 0
        ),
        "payments_by_class": payments_by_class,
        "today_payments": today_payments,
        "defaulter_counts": defaulter_counts,
        "defaulter_counts_by_stage": defaulter_counts_by_stage,
        "total_defaulters": total_defaulters,
        "fees_by_stage": fees_by_stage,
        "canteen_by_stage": canteen_by_stage,
        "canteen_totals": canteen_totals,
        "fee_structure_by_stage": fee_structure_by_stage,
        "recent_expenses": recent_expenses,
        "today": today,
        "STAGE_GROUP_MAP": STAGE_GROUP_MAP,
    }
    return render(request, "accounts/accountant_dashboard.html", context)


@login_required
def record_payment(request, fee_id=None):
    if request.user.role != "accountant":
        return redirect("accounts:home")

    school = request.user.school
    search_query = request.GET.get("search", "")
    active_term = Term.objects.filter(school=school, is_active=True).first()
    if not active_term:
        messages.error(request, "No active term set.")
        return redirect("accounts:accountant_dashboard")

    academic_year = active_term.academic_year
    fee = None
    student = None
    search_results = None

    if fee_id:
        fee = get_object_or_404(
            StudentFee.objects.prefetch_related("dynamic_items"),
            id=fee_id,
            student__school=school,
        )
        student = fee.student
    elif search_query:
        query = search_query.strip()
        base = User.objects.filter(school=school, role="student", is_active=True)
        base = base.annotate(full_name=Concat("first_name", Value(" "), "last_name"))
        search_results = base.filter(
            Q(student_number__icontains=query)
            | Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(full_name__icontains=query)
        ).distinct()[:10]
        exact_match = User.objects.filter(
            school=school, role="student", student_number__iexact=search_query
        ).first()
        if exact_match:
            fee = (
                StudentFee.objects.filter(
                    student=exact_match,
                    term=active_term,
                    academic_year=str(academic_year),
                )
                .prefetch_related("dynamic_items")
                .first()
            )
            if fee:
                student = exact_match
                search_results = None

    # Build fee_map
    real_balance = Decimal("0")
    real_paid = Decimal("0")
    fee_map = {}
    if fee:
        for item in fee.dynamic_items.all():
            balance_item = item.amount_due - item.amount_paid
            fee_map[item.name] = {
                "id": item.id,
                "due": item.amount_due,
                "paid": item.amount_paid,
                "balance": balance_item,
            }
            real_balance += balance_item
            real_paid += item.amount_paid

    if request.method == "POST" and fee:
        total_amount = Decimal("0")
        allocations = []
        for item in fee.dynamic_items.all():
            raw = request.POST.get(f"item_{item.id}", "").strip()
            if not raw:
                continue
            try:
                amt = Decimal(raw)
            except:
                continue
            if amt <= 0:
                continue
            if amt > (item.amount_due - item.amount_paid):
                messages.error(
                    request,
                    f"{item.name} exceeds balance GH₵ {item.amount_due - item.amount_paid}",
                )
                return redirect("accounts:record_payment", fee_id=fee.id)
            allocations.append((item, amt))
            total_amount += amt

        if total_amount <= 0:
            messages.error(request, "Enter at least one amount > 0")
            return redirect("accounts:record_payment", fee_id=fee.id)

        try:
            with transaction.atomic():
                payment_txn = PaymentTransaction.objects.create(
                    student=fee.student,
                    student_fee=fee,
                    school=school,
                    term=active_term,
                    academic_year=active_term.academic_year,
                    total_amount=total_amount,
                    amount_paid=total_amount,
                    payment_method="cash",
                    recorded_by=request.user,
                )

                for item_obj, amt in allocations:
                    PaymentItem.objects.create(
                        transaction=payment_txn,
                        fee_name=item_obj.name,
                        amount=amt,
                        payment_type=item_obj.payment_type,
                    )
                    item_obj.amount_paid += amt
                    item_obj.save(update_fields=["amount_paid"])
                    code = item_obj.name.upper().replace(" ", "_")
                    pt, _ = PaymentType.objects.get_or_create(
                        school=school, code=code, defaults={"name": item_obj.name}
                    )
                    payment_txn.payment_types.add(pt)

                FeeAuditLog.objects.create(
                    transaction=payment_txn,
                    action="COLLECTED",
                    done_by=request.user,
                    details=f"Collected GHS {total_amount} for {fee.student.get_full_name()}",
                )

            messages.success(
                request,
                f"Payment GH₵ {total_amount} recorded. Receipt: {payment_txn.receipt_number}",
            )
            return redirect("accounts:payment_history")
        except Exception as e:
            messages.error(request, f"Error: {e}")
            return redirect("accounts:record_payment", fee_id=fee.id)

    context = {
        "fee": fee,
        "student": student,
        "balance": real_balance,
        "total_paid": real_paid,
        "term": active_term.get_term_number_display(),
        "academic_year": academic_year,
        "active_term": active_term,
        "search_query": search_query,
        "fee_map": fee_map,
        "search_results": search_results,
    }
    return render(request, "accounts/record_payment.html", context)


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
    if request.user.role not in ['admin', 'accountant', 'bursar', 'headmaster', 'headmistress', 'proprietor', 'proprietress']:
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
    if request.user.role not in ['admin', 'bursar', 'headmaster', 'headmistress', 'proprietor', 'proprietress']:
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
        return redirect("accounts:expense_list")

    if expense.recorded_by != request.user:  # ✅ FIXED! recorded_by not created_by
        messages.error(request, "You can only delete your own expenses")
        return redirect("accounts:expense_list")

    if request.method == "POST":
        expense.delete()
        messages.success(request, "Expense deleted")
        return redirect("accounts:expense_list")

    return render(request, "accounts/confirm_delete.html", {"expense": expense})


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

    # === FILTER PART - NEW ===
    selected_term_id = request.GET.get('term')
    selected_year = request.GET.get('academic_year')

    if selected_term_id:
        active_term = Term.objects.filter(id=selected_term_id, school=school).first()
    else:
        active_term = Term.objects.filter(school=school, is_active=True).first()

    if not active_term:
        messages.error(request, "No active term set. Ask admin to set one.")
        return redirect('accounts:accountant_dashboard')

    # Year from filter or from term
    if selected_year:
        academic_year_str = selected_year
        academic_year_obj = active_term.academic_year # for generation still need object? we use str
    else:
        academic_year_str = str(active_term.academic_year)
        selected_year = academic_year_str

    academic_year = active_term.academic_year # keep object for later if needed

    # === For dropdowns (10 years, 1000 years data) ===
    all_terms = Term.objects.filter(school=school).order_by('-academic_year__name', 'term_number')
    all_years = AcademicYear.objects.filter(school=school).order_by('-name')

    if request.method == 'POST':
        # Generate fees from DynamicFeeStructure (same as before)
        class_id = request.POST.get('school_class')
        stage = SchoolClass.objects.get(id=class_id).stage if class_id else request.POST.get('stage')

        fee_structure = DynamicFeeStructure.objects.filter(
            school=school, stage=stage, term=active_term, academic_year=str(academic_year_str), is_published=True
        ).first()

        if not fee_structure:
            messages.error(request, f"No published fee structure for {stage} - {active_term} - {academic_year_str}")
            return redirect('accounts:set_student_fee')

        students_in_class = User.objects.filter(school=school, role='student', school_class_id=class_id, is_active=True) if class_id else User.objects.filter(school=school, role='student', is_active=True)

        for student in students_in_class:
            student_fee, created = StudentFee.objects.update_or_create(
                student=student,
                school=school,
                term=active_term,
                academic_year=str(academic_year_str),
                stage=stage,
                defaults={'total_amount': fee_structure.total_amount}
            )
            for item in fee_structure.items.all():
                DynamicStudentFeeItem.objects.update_or_create(
                    school=school,
                    student_fee=student_fee,
                    student=student,
                    name=item.name,
                    defaults={'amount_due': item.amount, 'payment_type': item.payment_type}
                )

        messages.success(request, f"Fees generated for {students_in_class.count()} students - {stage} - {academic_year_str}")
        return redirect(f"{request.path}?term={active_term.id}&academic_year={academic_year_str}")

    # GET - Filtered fee structure
    fee_structures = DynamicFeeStructure.objects.filter(
        term=active_term, 
        academic_year=str(academic_year_str), 
        school=school, 
        is_published=True
    ).prefetch_related('items')

    fee_structure_by_stage = {}
    for fs in fee_structures:
        breakdown = {item.name: float(item.amount) for item in fs.items.all()}
        breakdown["total"] = float(fs.total_amount)
        fee_structure_by_stage[fs.stage] = breakdown

    context = {
        'students': students,
        'classes': classes,
        'term': active_term,
        'academic_year': str(academic_year_str),
        'fee_structure_by_stage': fee_structure_by_stage,
        # FILTER DATA
        'all_terms': all_terms,
        'all_years': all_years,
        'selected_term_id': str(active_term.id) if active_term else "",
        'selected_year': academic_year_str,
    }
    return render(request, 'accounts/set_student_fee.html', context)


@login_required
def all_students_fees_list(request):
    if request.user.role != "accountant":
        return redirect("accounts:home")

    school = request.user.school

    active_term = Term.objects.filter(school=school, is_active=True).first()
    if not active_term:
        messages.error(
            request, "No active term set. Ask admin to set one in Term Settings."
        )
        return render(
            request,
            "accounts/all_students_fees_list.html",
            {
                "class_data": [],
                "total_owing": 0,
                "total_defaulters": 0,
                "active_term": None,
                "generated_on": date.today(),
            },
        )

    # FIX: academic_year as string for StudentFee (CharField)
    academic_year_obj = active_term.academic_year
    academic_year_str = str(
        academic_year_obj.name
        if hasattr(academic_year_obj, "name")
        else academic_year_obj
    )

    classes = SchoolClass.objects.filter(school=school).prefetch_related(
        Prefetch(
            "students",
            queryset=User.objects.filter(role="student", is_active=True).select_related(
                "school_class"
            ),
            to_attr="students_list",
        )
    )

    class_data = []
    total_owing = 0
    total_defaulters = 0
    generated_on = date.today()

    # FIX: Use string for year + school isolation
    all_payments = (
        PaymentTransaction.objects.filter(
            student__school=school,
            school=school,
            term=active_term,
            academic_year=academic_year_obj,
        )
        .values("student_id")
        .annotate(total_paid=Sum("total_amount"))
    )

    student_paid_map = {
        p["student_id"]: float(p["total_paid"] or 0) for p in all_payments
    }

    for school_class in classes:
        students = school_class.students_list
        if not students:
            continue

        student_ids = [s.id for s in students]
        fees = StudentFee.objects.filter(
            student_id__in=student_ids,
            school=school,  # isolation
            term_id=active_term.id,
            academic_year=academic_year_str,  # FIXED STRING
        ).select_related("student")

        fees_by_student = {f.student_id: f for f in fees}

        students_data = []
        class_total = 0

        for student in students:
            fee = fees_by_student.get(student.id)
            if not fee:
                continue

            paid = student_paid_map.get(student.id, 0)
            balance = float(fee.total_amount) - paid

            if balance > 0:
                students_data.append(
                    {
                        "student": student,
                        "student_number": student.student_number,
                        "total": fee.total_amount,
                        "paid": paid,
                        "balance": balance,
                        "fee_id": fee.id,
                        "status": "Owing",
                    }
                )
                total_owing += balance
                class_total += balance
                total_defaulters += 1

        if students_data:
            class_data.append(
                {
                    "class_name": school_class.name,
                    "stage": school_class.stage,  # ADDED for Lower / Upper print
                    "students": students_data,
                    "class_id": school_class.id,
                    "count": len(students_data),
                    "total_owing": class_total,
                }
            )

    context = {
        "class_data": class_data,
        "current_term": active_term.get_term_number_display(),
        "current_year": academic_year_str,
        "active_term": active_term,
        "total_owing": total_owing,
        "total_defaulters": total_defaulters,
        "page_title": "Fee Defaulters Report",
        "generated_on": generated_on,
        "school": school,
    }
    return render(request, "accounts/all_students_fees_list.html", context)


@login_required
def payment_history(request):
    if request.user.role != 'accountant':
        messages.error(request, "Not authorized.")
        return redirect('accounts:dashboard')

    school = request.user.school

    # ISOLATION: direct school filter
    transactions = PaymentTransaction.objects.filter(
        school=school,
        student__school=school
    ).select_related(
        'student',
        'recorded_by',
        'student__school_class',
        'term',
        'academic_year'
    ).prefetch_related(
        'payment_types',
        'items' # dynamic items
    ).order_by('-created_at')

    grouped = defaultdict(list)

    for tx in transactions:
        # DYNAMIC BALANCE - from DynamicStudentFeeItem, not hard-coded fields
        dyn_items = DynamicStudentFeeItem.objects.filter(
            school=school,
            student=tx.student,
            student_fee__term=tx.term,
            student_fee__academic_year=str(tx.academic_year)  # StudentFee academic_year is CharField
        )

        if dyn_items.exists():
            total_due = sum(float(i.amount_due) for i in dyn_items)
            total_paid = sum(float(i.amount_paid) for i in dyn_items)
            tx.fee_balance = total_due - total_paid
            tx.fee_total = total_due
            tx.fee_total_paid = total_paid
        else:
            # fallback to old StudentFee if dynamic not created yet
            try:
                fee = StudentFee.objects.get(
                    school=school,
                    student=tx.student,
                    term=tx.term,
                    academic_year=str(tx.academic_year)
                )
                tx.fee_balance = float(fee.balance())
                tx.fee_total = float(fee.total_amount)
                tx.fee_total_paid = float(tx.amount_paid) 

                tx.student_fee = fee
            except StudentFee.DoesNotExist:
                tx.fee_balance = 0
                tx.fee_total = float(tx.total_amount)
                tx.student_fee = None

        tx.payment_list = [pt.name for pt in tx.payment_types.all()] or [i.fee_name for i in tx.items.all()]

        class_name = tx.student.school_class.name if tx.student.school_class else "Unassigned"
        grouped[class_name].append(tx)

    context = {
        'transactions': transactions,
        'grouped_transactions': dict(grouped),
        'school': school,
        'active_term': Term.objects.filter(school=school, is_active=True).first(),
    }
    return render(request, 'accounts/payment_history.html', context)


@login_required
def view_receipt(request, payment_id):
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related(
            "student", "recorded_by", "student__school_class"
        ),
        id=payment_id,
        student__school=request.user.school,
    )

    # === FIX: Get breakdown from PaymentItem ===
    payment_items = PaymentItem.objects.filter(transaction=payment)
    fee_items = [(item.fee_name, float(item.amount)) for item in payment_items]

    if not fee_items:
        fee_items = [("School Fees", float(payment.total_amount))]

    # === FIX: Balance left ===
    student_fee = (
        StudentFee.objects.filter(
            student=payment.student,
            term=payment.term,
            academic_year=str(payment.academic_year),
        )
        .prefetch_related("dynamic_items")
        .first()
    )

    total_balance = 0
    if student_fee:
        for di in student_fee.dynamic_items.all():
            total_balance += float(di.amount_due - di.amount_paid)

    # ==================================================
    # QR CODE
    # ==================================================

    verification_url = request.build_absolute_uri(
        f"/verify-receipt/{payment.verification_token}/"
    )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )

    qr.add_data(verification_url)
    qr.make(fit=True)

    qr_image = qr.make_image(fill_color="black", back_color="white")

    qr_buffer = BytesIO()
    qr_image.save(qr_buffer, format="PNG")

    qr_code = base64.b64encode(qr_buffer.getvalue()).decode("utf-8")

    # ==================================================

    context = {
        "payment": payment,
        "school": payment.student.school,
        "accountant": payment.recorded_by,
        "fee_items": fee_items,
        "balance": total_balance,
        "total_paid": payment.total_amount,
        # QR CODE
        "qr_code": qr_code,
        "verification_url": verification_url,
    }

    return render(request, "accounts/receipt.html", context)


@login_required
def verify_payment(request):
    if request.user.role != "accountant":
        return redirect("accounts:home")

    school = request.user.school
    query = request.GET.get("query", "")

    receipt = None
    student = None
    payments = None

    if query:
        # 1. Try receipt search first - ISOLATED
        receipt = (
            PaymentTransaction.objects.filter(
                school=school, receipt_number__iexact=query, student__school=school
            )
            .select_related("student", "student__school_class")
            .first()
        )

        # 2. If receipt found
        if receipt:
            student = receipt.student
            payments = PaymentTransaction.objects.filter(
                school=school,
                student=student,
                student__school=school,
                term=receipt.term,
                academic_year=receipt.academic_year,
            ).order_by("-created_at")

        else:
            # 3. Student search fallback - ISOLATED
            student = (
                User.objects.filter(
                    school=school,
                    role="student",
                    is_active=True
                )
                .annotate(
                    full_name=Concat(
                        "first_name",
                        Value(" "),
                        "last_name"
                    )
                )
                .filter(
                    Q(student_number__iexact=query)
                    | Q(first_name__icontains=query)
                    | Q(last_name__icontains=query)
                    | Q(full_name__icontains=query)
                )
                .first()
            )

            if student:
                payments = PaymentTransaction.objects.filter(
                    school=school, student=student, student__school=school
                ).order_by("-created_at")

        # ADD DYNAMIC BALANCE TO EACH PAYMENT
        if payments:
            for tx in payments:
                dyn_items = DynamicStudentFeeItem.objects.filter(
                    school=school,
                    student=tx.student,
                    student_fee__term=tx.term,
                    student_fee__academic_year=str(tx.academic_year),
                )
                if dyn_items.exists():
                    total_due = sum(float(i.amount_due) for i in dyn_items)
                    total_paid = sum(float(i.amount_paid) for i in dyn_items)
                    tx.fee_total_paid = total_paid
                    tx.fee_balance = total_due - total_paid
                    tx.fee_total = total_due
                else:
                    tx.fee_total_paid = float(tx.amount_paid)
                    tx.fee_balance = 0
                    tx.fee_total = float(tx.total_amount)

    context = {
        "query": query,
        "receipt": receipt,
        "student": student,
        "payments": payments,
    }
    return render(request, "accounts/verify_payment.html", context)

def verify_receipt(request, verification_token):
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related(
            "student",
            "recorded_by",
            "student__school_class",
            "term",
            "academic_year",
        ),
        verification_token=verification_token,
    )

    context = {
        "payment": payment,
        "school": payment.student.school,
        "student": payment.student,
        "student_number": payment.student.student_number,
        "receipt_number": payment.receipt_number,
        "amount_paid": payment.total_amount,
        "payment_method": payment.get_payment_method_display(),
    }

    return render(
        request,
        "accounts/verify_receipt.html",
        context
    )


@login_required
def generate_report(request):
    from xhtml2pdf import pisa
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
    from xhtml2pdf import pisa
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
    fee_structures = DynamicFeeStructure.objects.filter(school=school, is_published=True)
    
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
        stage_group = request.POST.get('stage_group')
        due_date = request.POST.get('due_date')

        structure = get_object_or_404(DynamicFeeStructure, id=structure_id, school=school)
        
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
            # 1. Create StudentFee header
            student_fee, created_flag = StudentFee.objects.update_or_create(
                student=student,
                term=structure.term,
                academic_year=structure.academic_year,
                defaults={
                    'school': school,
                    'total_amount': structure.total_amount,
                    'due_date': due_date
                }
            )
            # 2. Delete old breakdown and recreate dynamic breakdown
            student_fee.dynamic_items.all().delete()
            for item in structure.items.all():
                DynamicStudentFeeItem.objects.create(
                    school=school,
                    student_fee=student_fee,
                    payment_type=item.payment_type,
                    name=item.name,
                    amount=item.amount
                )

            if created_flag:
                created += 1
            else:
                updated += 1

        messages.success(request, f"Applied {structure} to {created} new, {updated} updated students in {stage_group}")
        return redirect('accounts:apply_fee_structure')

    context = {
        'fee_structures': fee_structures,
        'stage_groups': DynamicFeeStructure.STAGE_CHOICES,  
    }
    return render(request, 'accounts/apply_fee_structure.html', context)


from django.urls import reverse


@login_required
def fee_structure_settings(request):
    if request.user.role not in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]:
        messages.error(request, "Only admin and headmaster can access this page.")
        return redirect('accounts:dashboard')

    school = request.user.school

    # Use DynamicFeeStructure for years
    years = DynamicFeeStructure.objects.filter(school=school).values_list('academic_year', flat=True).distinct().order_by('-academic_year')
    if not years:
        # fallback to old FeeStructure for years
        years = FeeStructure.objects.filter(school=school).values_list('academic_year', flat=True).distinct().order_by('-academic_year')

    selected_year = request.GET.get('year')
    if not selected_year:
        active_term = Term.objects.filter(is_active=True, school=school).first()
        if active_term:
            selected_year = active_term.academic_year.name if hasattr(active_term.academic_year, 'name') else str(active_term.academic_year)
        else:
            selected_year = years.first() if years else ''

    # For list table
    fee_structures = DynamicFeeStructure.objects.filter(school=school, academic_year=selected_year).order_by('stage', 'term') if selected_year else DynamicFeeStructure.objects.filter(school=school).order_by('-created_at')

    if request.method == 'POST' and 'create_fee_structure' in request.POST:
        stage = request.POST.get('stage')
        term_id = request.POST.get('term')
        academic_year = request.POST.get('academic_year')
        due_date = request.POST.get('due_date') or None

        term = Term.objects.filter(id=term_id, school=school).first()
        if not term:
            # try term_number
            term = Term.objects.filter(term_number=term_id, school=school).first()

        if not term:
            messages.error(request, "Term not found for this school.")
            return redirect('accounts:fee_structure_settings')

        fee_names = request.POST.getlist("fee_name[]")
        fee_amounts = request.POST.getlist("fee_amount[]")

        def to_decimal(val):
            try:
                return Decimal(val) if val not in [None, ''] else Decimal('0')
            except:
                return Decimal('0')

        # Create or update fee structure
        fee_struct, created = DynamicFeeStructure.objects.update_or_create(
            school=school,
            stage=stage,
            term=term,
            academic_year=academic_year,
            defaults={
                'due_date': due_date,
                'is_published': False,
            }
        )

        # If updating, clear old items
        if not created:
            fee_struct.items.all().delete()

        saved_count = 0
        for name, amount in zip(fee_names, fee_amounts):
            name = name.strip()
            if not name or not amount:
                continue
            amt = to_decimal(amount)
            if amt <= 0:
                continue

            # Auto create PaymentType for this school
            code = name.lower().replace(" ", "_").replace("-", "_")
            payment_type, _ = PaymentType.objects.get_or_create(
                school=school,
                code=code,
                defaults={"name": name}
            )

            DynamicFeeStructureItem.objects.update_or_create(
                school=school,
                fee_structure=fee_struct,
                name=name,
                defaults={
                    'amount': amt,
                    'payment_type': payment_type
                }
            )
            saved_count += 1

        if saved_count == 0:
            fee_struct.delete()
            messages.error(request, "Add at least one valid fee item.")
        else:
            messages.success(request, f"Fee for {fee_struct.get_stage_display()} - {term} - {academic_year} saved with {saved_count} items. Total: GHS {fee_struct.total_amount}")

        request.session['last_fee_stage'] = stage
        request.session['last_fee_term'] = term.id
        request.session['last_fee_year'] = academic_year

        return redirect(f"{reverse('accounts:fee_structure_settings')}?stage={stage}&term={term.id}&year={academic_year}")

    # For summary display
    selected_stage = request.GET.get('stage')
    selected_term_id = request.GET.get('term')
    selected_year_param = request.GET.get('year') or selected_year

    fee_breakdown = None
    if selected_stage and selected_term_id and selected_year_param:
        fee_breakdown = DynamicFeeStructure.objects.filter(
            school=school,
            stage=selected_stage,
            term_id=selected_term_id,
            academic_year=selected_year_param,
        ).prefetch_related('items').first()
        if fee_breakdown:
            request.session['last_fee_stage'] = selected_stage
            request.session['last_fee_term'] = selected_term_id
            request.session['last_fee_year'] = selected_year_param

    if not fee_breakdown:
        last_stage = request.session.get('last_fee_stage')
        last_term = request.session.get('last_fee_term')
        last_year = request.session.get('last_fee_year')
        if last_stage and last_term and last_year:
            fee_breakdown = DynamicFeeStructure.objects.filter(
                school=school,
                stage=last_stage,
                term_id=last_term,
                academic_year=last_year,
            ).prefetch_related('items').first()

        if not fee_breakdown:
            fee_breakdown = DynamicFeeStructure.objects.filter(
                school=school
            ).order_by('-id').prefetch_related('items').first()

    return render(request, 'accounts/fee_structure_settings.html', {
        'school': school,
        'fee_structures': fee_structures,
        'years': years,
        'selected_year': selected_year,
        'stage_choices': STAGE_CHOICES,
        'fee_breakdown': fee_breakdown,
    })


def is_admin_or_headmaster(user):
    return user.role in  [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]
@login_required
@user_passes_test(is_admin_or_headmaster) #
def generate_fees_for_term(request):
    school = request.user.school

    if request.method == "POST":
        stage_group = request.POST.get("stage_group")
        academic_year_str = request.POST.get("academic_year")

        if not academic_year_str:
            messages.error(request, "Invalid academic year.")
            return redirect("accounts:fee_structure_settings")

        term_id = request.POST.get("term")
        term_obj = Term.objects.filter(school=school, id=term_id).first()

        if not term_obj:
            messages.error(request, "Term not found.")
            return redirect("accounts:fee_structure_settings")

        STAGE_GROUP_MAP = {
            "creche": ["Creche"],
            "nursery": ["Nursery 1", "Nursery 2"],
            "kg": ["KG 1", "KG 2"],
            "lower_primary": ["Primary 1", "Primary 2", "Primary 3"],
            "upper_primary": ["Primary 4", "Primary 5", "Primary 6"],
            "jhs": ["JHS 1", "JHS 2", "JHS 3"],
            "shs": ["SHS 1", "SHS 2", "SHS 3"],
        }

        # NEW: Use DynamicFeeStructure
        fee_structure = (
            DynamicFeeStructure.objects.filter(
                school=school,
                stage=stage_group,
                term=term_obj,
                academic_year=academic_year_str,
            )
            .prefetch_related("items")
            .first()
        )

        if not fee_structure:
            messages.error(
                request,
                f"No fee structure set for {stage_group} {academic_year_str}. Save it first.",
            )
            return redirect("accounts:fee_structure_settings")

        # Publish it
        fee_structure.is_published = True
        fee_structure.save(update_fields=["is_published"])

        # Get students in those classes - YOUR SAME LOGIC
        students = User.objects.filter(
            school=school,
            school_class__stage=stage_group,
            role__iexact="student",
            is_active=True,
        ).select_related("school_class")

        if not students.exists():
            messages.warning(request, f"No students found in {stage_group}.")
            return redirect("accounts:fee_structure_settings")

        updated = 0
        created = 0

        for student in students:
            # IMPORTANT: update_or_create to avoid duplicate fee
            student_fee, created_flag = StudentFee.objects.update_or_create(
                student=student,
                school=school,
                term=term_obj,
                academic_year=academic_year_str,
                stage=stage_group,  # added stage for safety
                defaults={
                    "total_amount": sum([item.amount for item in fee_structure.items.all()]),
                    "due_date": fee_structure.due_date,
                },
            )
            if created_flag:
                created += 1
            else:
                updated += 1
                # If updating, delete old dynamic items to avoid duplicate
                student_fee.dynamic_items.all().delete()

            # Create dynamic breakdown for this student
            for fs_item in fee_structure.items.all():
                DynamicStudentFeeItem.objects.create(
                    school=school,
                    student_fee=student_fee,
                    student=student,
                    name=fs_item.name,
                    amount_due=fs_item.amount,
                    amount_paid=0,
                    payment_type=fs_item.payment_type,
                )

        messages.success(
            request,
            f"Done. Created {created} new fees, updated {updated} existing fees for {stage_group} - {term_obj} - {academic_year_str}. Per student Total: GHS {fee_structure.total_amount}",
        )
        return redirect("accounts:fee_structure_settings")
    return redirect("accounts:fee_structure_settings")


def is_admin_headmaster(user):
    return user.role in [
        'admin',
        'proprietor',
        'proprietress',
        'headmaster',
        'headmistress'
    ]

@login_required
@user_passes_test(is_admin_headmaster) # ✅ All 3 can see fees list
def student_fees_list(request):
    school = request.user.school  
    
    term_id = request.GET.get('term', '')  
    year_id = request.GET.get('year')
    search = request.GET.get('search', '')
    new_only = request.GET.get('new_only') == 'on'
    
    terms = Term.objects.filter(school=school).order_by('term_number')  
    year_choices = AcademicYear.objects.filter(school=school).order_by('-name')
    
    current_year = AcademicYear.objects.filter(school=school).order_by('-name').first()
    current_term = Term.objects.filter(school=school, is_active=True).first()

    # Default to current if not selected
    if not year_id and current_year:
        year_id = str(current_year.id)
    if not term_id and current_term:
        term_id = str(current_term.id)
    
    # Base queryset - NEW: prefetch dynamic_items for breakdown
    fees = StudentFee.objects.filter(school=school).select_related('student', 'term', 'student__school_class').prefetch_related('dynamic_items')
    
    selected_year = None
    if year_id and year_id.isdigit():
        selected_year = year_choices.filter(id=year_id).first()
        if selected_year:
            fees = fees.filter(academic_year=selected_year.name)
    
    if term_id and term_id.isdigit():
        fees = fees.filter(term_id=term_id)
    
    if search:
        fees = fees.filter(
            Q(student__first_name__icontains=search) |
            Q(student__last_name__icontains=search) |
            Q(student__student_number__icontains=search)
        )
    
    if new_only and selected_year:

        pass
    
    selected_term_id = term_id if term_id and term_id.isdigit() else ''
    selected_term_obj = terms.filter(id=selected_term_id).first() if selected_term_id else None
    
    if not selected_year and year_id and year_id.isdigit():
        selected_year = year_choices.filter(id=year_id).first()

    context = {
        'fees': fees.order_by('student__first_name'),
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
@user_passes_test(is_admin_headmaster)
def edit_student_fee(request, fee_id):
    school = request.user.school
    fee = get_object_or_404(
        StudentFee.objects.select_related('student','term').prefetch_related('dynamic_items'), 
        id=fee_id, school=school
    ) 

    items = fee.dynamic_items.all()

    if request.method == 'POST':
        due_date = request.POST.get('due_date')
        if due_date:
            fee.due_date = due_date

        total = 0
        for item in items:
            amount_key = f"item_{item.id}"
            paid_key = f"paid_{item.id}"

            if amount_key in request.POST:
                try:
                    new_amount = float(request.POST.get(amount_key) or 0)
                    item.amount_due = new_amount
                except:
                    pass

            if paid_key in request.POST:
                try:
                    new_paid = float(request.POST.get(paid_key) or 0)
                    item.amount_paid = new_paid
                except:
                    pass

            item.save()
            total += float(item.amount_due)

        fee.total_amount = total
        fee.save(update_fields=['total_amount', 'due_date'])

        messages.success(request, f"Fees updated for {fee.student.get_full_name()} - New total GH₵ {total}")
        return redirect(f"{reverse('accounts:student_fees_list')}?term={fee.term.id}")  

    return render(request, 'accounts/edit_student_fee.html', {
        'fee': fee,
        'items': items,
    })


def is_accountant_student_parent(user):
    return user.role in ['accountant', 'student', 'parent']
@login_required
@user_passes_test(is_accountant_student_parent) 
def receipt_pdf(request, payment_id):
    from xhtml2pdf import pisa

    school = request.user.school
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related(
            "student", "recorded_by", "student__school_class"
        ),
        id=payment_id,
        student__school=school,  
    )

    if request.user.role == 'student':
        if payment.student != request.user:
            return redirect('student:payment_history')

    if request.user.role == 'parent':
        if payment.student not in request.user.children.all():
            return redirect('parent:dashboard')
    try:
        payment_items = payment.items.all()
    except:
        payment_items = PaymentItem.objects.filter(transaction=payment)

    if not payment_items.exists():
        try:
            payment_items = payment.payment_items.all()
        except:
            pass

    if not payment_items.exists():
        display_text = payment.get_payment_types_display()
        if display_text == "—":
            fee_items = [("School Fees", float(payment.total_amount))]
        else:
            names = [n.strip() for n in display_text.split("\n") if n.strip()]
            amount_per_item = (
                float(payment.total_amount) / len(names)
                if names
                else float(payment.total_amount)
            )
            fee_items = [(name, amount_per_item) for name in names]
    else:
        fee_items = [(item.fee_name, float(item.amount)) for item in payment_items]

    student_fee = (
        StudentFee.objects.filter(
            student=payment.student,
            term=payment.term,
            academic_year=str(payment.academic_year),
        )
        .prefetch_related("dynamic_items")
        .first()
    )

    total_balance = 0
    if student_fee:
        for di in student_fee.dynamic_items.all():
            total_balance += float(di.amount_due - di.amount_paid)
        # =========================================================
    # QR CODE VERIFICATION
    # =========================================================

    verification_url = request.build_absolute_uri(
        reverse(
            "accounts:verify_payment",
            kwargs={
                "token": str(payment.verification_token)
            }
        )
    )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )

    qr.add_data(verification_url)
    qr.make(fit=True)

    qr_image = qr.make_image(fill_color="black", back_color="white")

    qr_buffer = BytesIO()
    qr_image.save(qr_buffer, format="PNG")

    qr_base64 = base64.b64encode(
        qr_buffer.getvalue()
    ).decode("utf-8")

    qr_code_data = f"data:image/png;base64,{qr_base64}"

    context = {
        "payment": payment,
        "school": school,
        "accountant": payment.recorded_by,
        "total_paid": payment.total_amount,
        "fee_items": fee_items,
        "balance": total_balance,  # your receipt.html uses {{ balance }}
        "qr_code_data": qr_code_data,  # Add this line
    }

    # === YOUR CORRECT TEMPLATE IS receipt.html NOT receipt_pdf.html ===
    html_string = render_to_string("accounts/receipt.html", context)
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = (
        f"inline; filename=receipt-{payment.receipt_number}.pdf"
    )

    pisa_status = pisa.CreatePDF(BytesIO(html_string.encode("UTF-8")), dest=response)
    if pisa_status.err:
        return HttpResponse(f"PDF error: {pisa_status.err}", status=500)
    return response


def get_success_url(self):
    user = self.request.user
    role = getattr(user, "role", None)
    if role in ["admin", "proprietor", "proprietress"]:
        return "/accounts/dashboard/"
    if role in ["headmaster", "headmistress"]:
        return "/accounts/dashboard/"  # or headmaster dashboard
    if role == "accountant":
        return "/accountant/"
    if role == "teacher":
        return "/teacher/dashboard/"
    if role == "student":
        return "/student/dashboard/"
    if user.is_superuser:
        return "/admin/"
    return "/accounts/dashboard/"


from django.views.decorators.http import require_GET

@login_required
@require_GET  
def mark_announcements_read(request):
    ann_id = request.GET.get('id')
    if ann_id:
        announcement = get_object_or_404(
            Announcement, 
            pk=ann_id, 
            school=request.user.school
        )
        announcement.acknowledged_by.add(request.user)
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'No ID provided'})

@login_required
def acknowledge_announcement(request, pk):
    announcement = get_object_or_404(
        Announcement, 
        pk=pk, 
        school=request.user.school
    )
    
    # Check if this announcement is for you
    if announcement.target_roles:
        target_roles = [r.lower() for r in announcement.target_roles]
        if 'all' not in target_roles and request.user.role.lower() not in target_roles:
            messages.error(request, "Not for you")
            return redirect('accounts:student_dashboard')
    
    announcement.acknowledged_by.add(request.user)
    
    AnnouncementRead.objects.get_or_create(
        announcement=announcement,
        user=request.user
    )
    
    if announcement.action_url:
        return redirect(announcement.action_url)
    
    return redirect('accounts:student_dashboard')


from .sms_service import send_sms_to_school, format_ghana_number

def is_admin_headmaster(user):
    return user.role in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]

@login_required
@user_passes_test(is_admin_headmaster) # ✅ FIX HERE!
def create_announcement(request):
    # your code same...
    if request.method == "POST":
        form = AnnouncementForm(request.POST, school=request.user.school)
        send_sms = request.POST.get("send_sms") == "on"

        if form.is_valid():
            ann = form.save(commit=False)
            ann.school = request.user.school
            ann.created_by = request.user
            ann.target_roles = form.cleaned_data["target_roles"]
            ann.save()
            selected_classes = list(form.cleaned_data.get('target_classes') or [])
            if selected_classes:
                ann.target_classes.set(selected_classes)
                ann.target_class_id = selected_classes[0].id
                ann.save()
            else:
                ann.target_classes.clear()
                ann.target_class_id = None
                ann.save()

            target_roles = [r.lower() for r in (ann.target_roles or [])]

            users = User.objects.none()
            group_name = ""

            if "all" in target_roles:
                users = User.objects.filter(school=request.user.school)
                group_name = "All Users"
            else:
                q = Q()
                for r in target_roles:
                    if r == "specific class":
                        continue
                    q |= Q(role__icontains=r)
                if q:
                    users = User.objects.filter(school=request.user.school).filter(q)
                group_name = (
                    ", ".join(
                        [r for r in target_roles if r != "specific class"]
                    ).title()
                    or "Specific Class"
                )

                if "student" in target_roles:
                    student_qs = users.filter(role__icontains="student")
                    # Also auto-add parents of those students
                    for stu in student_qs:
                        try:
                            if hasattr(stu, "parent") and stu.parent:
                                users = users | User.objects.filter(id=stu.parent.id)
                        except:
                            pass

            # ===== FIXED: Handle BOTH old and new class fields =====
            has_new_classes = ann.target_classes.exists()
            has_old_class = ann.target_class_id is not None

            if has_new_classes or has_old_class:
                if has_new_classes:
                    class_names = ", ".join([c.name for c in ann.target_classes.all()])
                    class_ids = list(ann.target_classes.values_list("id", flat=True))
                else:
                    class_names = ann.target_class.name
                    class_ids = [ann.target_class_id]

                group_name = (
                    f"{class_names}"
                    if "all" in target_roles
                    else f"{group_name} - {class_names}"
                )

                # Filter students to only those classes
                if "student" in target_roles or "specific class" in target_roles:
                    non_students = users.exclude(role__icontains="student")
                    students_in_class = User.objects.filter(
                        school=request.user.school,
                        role__icontains="student",
                        school_class_id__in=class_ids,
                    )
                    print(
                        f"DEBUG class_ids={class_ids} found={students_in_class.count()} students"
                    )
                    # If 'all' or only specific class, include all students in class
                    if "all" in target_roles or not users.exists():
                        users = students_in_class
                        # Also add parents of those students if parent role wanted
                        if "parent" in target_roles or "all" in target_roles:
                            parent_ids = []
                            for stu in students_in_class:
                                if hasattr(stu, "parent") and stu.parent:
                                    parent_ids.append(stu.parent.id)
                            if parent_ids:
                                users = users | User.objects.filter(id__in=parent_ids)
                    else:
                        users = non_students | students_in_class

            notif = Notification.objects.create(
                school=request.user.school,
                title=ann.title,
                message=ann.message,
                created_by=request.user,
                target_group=group_name,
                announcement=ann,
            )
            users = users.distinct().exclude(id=request.user.id)

            NotificationRecipient.objects.bulk_create(
                [NotificationRecipient(notification=notif, user=u) for u in users],
                ignore_conflicts=True,
            )

            NotificationRecipient.objects.get_or_create(
                notification=notif, user=request.user
            )

            sms_result = ""
            if send_sms and request.user.school.sms_enabled:
                phone_numbers = []
                for u in users:
                    if u.phone:
                        phone_numbers.append(u.phone)
                    if hasattr(u, "parent_guardian_phone") and u.parent_guardian_phone:
                        phone_numbers.append(u.parent_guardian_phone)
                    if hasattr(u, "parent") and u.parent and u.parent.phone:
                        phone_numbers.append(u.parent.phone)

                sms_msg = f"{ann.title}: {ann.message[:100]}"

                success, result_msg = send_sms_to_school(
                    request.user.school, phone_numbers, sms_msg
                )
                if success:
                    sms_result = f" | SMS: {result_msg}"
                else:
                    sms_result = f" | SMS Failed: {result_msg}"

            messages.success(
                request, f"Sent to {users.count()} users: {group_name}{sms_result}"
            )
            return HttpResponseRedirect("/notifications/")
        else:
            print("FORM ERRORS:", form.errors)
            print("POST:", dict(request.POST))
    else:
        form = AnnouncementForm(school=request.user.school)
    return render(
        request,
        "accounts/create_announcement.html",
        {"form": form, "school": request.user.school},
    )


@login_required
def delete_announcement(request, pk):
    notif = Notification.objects.filter(
        pk=pk, 
        school=request.user.school
    ).first()
    
    if not notif:
        messages.error(request, "Not found")
        return HttpResponseRedirect('/notifications/')
    
    # Only creator or admin can delete
    if notif.created_by != request.user and request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        messages.error(request, "Not allowed")
        return HttpResponseRedirect('/notifications/')
    
    if request.method == 'POST':  
        Announcement.objects.filter(
            school=request.user.school,
            title=notif.title,
            created_by=notif.created_by
        ).delete()
        notif.delete()
        messages.success(request, "Deleted")
    
    return HttpResponseRedirect('/notifications/')

@login_required
@user_passes_test(is_admin_headmaster)
def edit_announcement(request, pk):
    notif = Notification.objects.filter(pk=pk, school=request.user.school).first()
    if not notif:
        return HttpResponseRedirect('/notifications/')
    
    ann = notif.announcement  # ✅ USE THIS NOW, not title filter!
    
    if request.method == 'POST':
        notif.title = request.POST.get('title')
        notif.message = request.POST.get('message')
        notif.save()
        if ann:
            ann.title = notif.title
            ann.message = notif.message
            ann.save()
        messages.success(request, "Updated")
        return HttpResponseRedirect('/notifications/')
    
    return render(request, 'accounts/edit_announcement.html', {'notif': notif, 'ann': ann})


from django.contrib.auth.decorators import login_required

@login_required
@user_passes_test(is_admin_headmaster) # admin + headmaster
def school_payment_settings(request):
    if not hasattr(request.user, 'school') or not request.user.school:
        messages.error(request, "No school assigned to your account")
        return redirect('accounts:dashboard')
    
    school = request.user.school  
    
    if request.method == 'POST':
        form = SchoolPaymentSettingsForm(request.POST, instance=school)
        if form.is_valid():
            form.save()
            messages.success(request, "Payment settings saved successfully!")
            return redirect('accounts:school_payment_settings')
    else:
        form = SchoolPaymentSettingsForm(instance=school)
    
    return render(request, 'accounts/school_payment_settings.html', {'form': form})

@login_required
def fee_list_page(request):
    if not hasattr(request.user, 'school') or not request.user.school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')
        
    school = request.user.school
    
    # NEW: Use DynamicFeeStructure
    fee_structures = DynamicFeeStructure.objects.filter(
        school=school, is_published=True
    ).select_related('term').prefetch_related('items').order_by('-created_at')
    
    years = DynamicFeeStructure.objects.filter(school=school).values_list('academic_year', flat=True).distinct().order_by('-academic_year')
    terms = Term.objects.filter(school=school).order_by('-id')
    
    # For summary card on top - latest fee
    fee_breakdown = DynamicFeeStructure.objects.filter(school=school, is_published=True).prefetch_related('items').first()
    
    selected_year = request.GET.get('year')
    selected_term = request.GET.get('term')
    
    if selected_year:
        fee_structures = fee_structures.filter(academic_year=selected_year)
        fee_breakdown = DynamicFeeStructure.objects.filter(school=school, academic_year=selected_year, is_published=True).prefetch_related('items').first()
        
    if selected_term:
        fee_structures = fee_structures.filter(term_id=selected_term)
    

    student_fees = StudentFee.objects.filter(school=school).select_related('student', 'term').prefetch_related('dynamic_items').order_by('-id')
    
    
    if selected_year:
        student_fees = student_fees.filter(academic_year=selected_year)
    if selected_term:
        student_fees = student_fees.filter(term_id=selected_term)

    # ADD HERE:
    if request.user.role == 'student':
        student_fees = student_fees.filter(student=request.user)
    elif request.user.role == 'parent':
        student_ids = ParentStudentLink.objects.filter(parent=request.user, school=school).values_list('student_id', flat=True)
        student_fees = student_fees.filter(student_id__in=student_ids)

    student_fees = student_fees[:100]  # LAST

    return render(request, 'accounts/finance_fee_list.html', {
        'fee_structures': fee_structures,
        'years': years,
        'terms': terms,
        'selected_year': selected_year,
        'selected_term': selected_term,
        'fee_breakdown': fee_breakdown,
        'student_fees': student_fees, # new for table
    })


@login_required
@require_POST
def verify_paystack_payment(request):
    try:
        data = json.loads(request.body)
        reference = data.get('reference')
        items = data.get('items', []) # [{fee_name, dynamic_item_id, fee_id, amount}]

        if not items:
            return JsonResponse({'status': False, 'message': 'No fees selected'})

        school = request.user.school
        if not school or not school.paystack_secret_key:
            return JsonResponse({'status': False, 'message': 'School payment not configured'})


        # Verify with Paystack
        r = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {school.paystack_secret_key}'},
            timeout=15
        )
        res = r.json()
        if not res.get('status') or res['data']['status']!= 'success':
            return JsonResponse({'status': False, 'message': 'Payment verification failed'})

        amount_ghs = Decimal(res['data']['amount']) / Decimal(100)
        items_total = sum(Decimal(str(i['amount'])) for i in items)
        if abs(items_total - amount_ghs) > Decimal('0.01'):
            return JsonResponse({'status': False, 'message': 'Amount mismatch'})

        # Get first fee to get term/year - school isolated
        first_item = items[0]
        fee = get_object_or_404(StudentFee, id=first_item["fee_id"], school=school)
        student = fee.student
        if request.user.role == 'student' and fee.student != request.user:
            return JsonResponse({'status': False, 'message': 'Not your fee'})
        if request.user.role == 'parent':
            if not ParentStudentLink.objects.filter(parent=request.user, student=fee.student, school=school).exists():
                return JsonResponse({'status': False, 'message': 'Not your child'})

        # ADD: Check all items belong to same student & same school
        for item in items:
            dyn_check = get_object_or_404(
                DynamicStudentFeeItem, id=item["dynamic_item_id"], school=school
            )
            if dyn_check.student != student:
                return JsonResponse({"status": False, "message": "Student mismatch"})

        ay_obj, _ = AcademicYear.objects.get_or_create(
            name=fee.academic_year,
            school=school, # isolation
            defaults={'is_active': False}
        )

        with transaction.atomic():
            txn = PaymentTransaction.objects.create(
                student=fee.student,
                school=school,
                total_amount=amount_ghs,
                amount_paid=amount_ghs,
                term=fee.term,
                academic_year=ay_obj,
                payment_method='paystack',
                transaction_id=reference,
                recorded_by=request.user
            )

            for item in items:
                fee_name = item.get('fee_name')
                dyn_id = item.get('dynamic_item_id')
                item_amount = Decimal(str(item['amount']))

                # DYNAMIC UPDATE - no paid_field!
                dyn_fee_item = get_object_or_404(
                    DynamicStudentFeeItem,
                    id=dyn_id,
                    school=school,
                    student=fee.student
                )
                dyn_fee_item.amount_paid = (dyn_fee_item.amount_paid or 0) + item_amount
                dyn_fee_item.save()

                # For PaymentItem history - dynamic
                payment_type_obj = dyn_fee_item.payment_type
                PaymentItem.objects.create(
                    transaction=txn,
                    payment_type=payment_type_obj,
                    fee_name=fee_name,
                    amount=item_amount
                )

            FeeAuditLog.objects.create(
                transaction=txn,
                action='COLLECTED',
                done_by=request.user,
                details=f"PAYSTACK Auto - GHS {amount_ghs} - Ref {reference} - {fee.student.get_full_name()} - {', '.join([i['fee_name'] for i in items])}"
            )

        # Return new balance
        total_bal = sum([i.balance for i in DynamicStudentFeeItem.objects.filter(student=fee.student, school=school, student_fee__term=fee.term)])

        return JsonResponse({
            'status': True,
            'receipt': txn.receipt_number,
            'balance': float(total_bal)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': False, 'message': str(e)}, status=500)


@login_required
@require_POST
def verify_hubtel_payment(request):
    try:
        data = json.loads(request.body)
        reference = data.get("reference")  # will be HUBTEL-TEST-...
        items = data.get("items", [])

        if not items:
            return JsonResponse({"status": False, "message": "No fees selected"})

        school = request.user.school
        if not reference.startswith("HUBTEL-TEST-"):
            return JsonResponse({"status": False, "message": "Invalid test ref"})
        # For TEST MODE, we don't check hubtel keys - allow fake keys

        # === WE SKIP Hubtel API verification for TEST MODE ===
        # Calculate total from items - like Paystack does
        amount_ghs = sum(Decimal(str(i["amount"])) for i in items)

        # Get first fee to get term/year - same as your Paystack
        first_item = items[0]
        fee = get_object_or_404(StudentFee, id=first_item["fee_id"], school=school)
        student = fee.student

        # ADD permission:
        if request.user.role == 'student' and student!= request.user:
            return JsonResponse({"status": False, "message": "Not your fee"})
        if request.user.role == 'parent':
            if not ParentStudentLink.objects.filter(parent=request.user, student=student, school=school).exists():
                return JsonResponse({"status": False, "message": "Not your child"})

        # ADD student mismatch:
        for item in items:
            dyn_check = get_object_or_404(DynamicStudentFeeItem, id=item["dynamic_item_id"], school=school)
            if dyn_check.student!= student:
                return JsonResponse({"status": False, "message": "Student mismatch"})

        ay_obj, _ = AcademicYear.objects.get_or_create(
            name=fee.academic_year, school=school, defaults={"is_active": False}
        )

        with transaction.atomic():
            txn = PaymentTransaction.objects.create(
                student=fee.student,
                school=school,
                total_amount=amount_ghs,
                amount_paid=amount_ghs,
                term=fee.term,
                academic_year=ay_obj,
                payment_method="hubtel",  # <-- only change here
                transaction_id=reference,
                recorded_by=request.user,
            )

            for item in items:
                fee_name = item.get("fee_name")
                dyn_id = item.get("dynamic_item_id")
                item_amount = Decimal(str(item["amount"]))

                dyn_fee_item = get_object_or_404(
                    DynamicStudentFeeItem, id=dyn_id, school=school, student=fee.student
                )
                dyn_fee_item.amount_paid = (dyn_fee_item.amount_paid or 0) + item_amount
                dyn_fee_item.save()

                payment_type_obj = dyn_fee_item.payment_type
                PaymentItem.objects.create(
                    transaction=txn,
                    payment_type=payment_type_obj,
                    fee_name=fee_name,
                    amount=item_amount,
                )

            FeeAuditLog.objects.create(
                transaction=txn,
                action="COLLECTED",
                done_by=request.user,
                details=f"HUBTEL TEST - GHS {amount_ghs} - Ref {reference} - {fee.student.get_full_name()} - {', '.join([i['fee_name'] for i in items])}",
            )

        total_bal = sum(
            [
                i.balance
                for i in DynamicStudentFeeItem.objects.filter(
                    student=fee.student, school=school, student_fee__term=fee.term
                )
            ]
        )

        return JsonResponse(
            {"status": True, "receipt": txn.receipt_number, "balance": float(total_bal)}
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return JsonResponse({"status": False, "message": str(e)}, status=500)


@login_required
@require_POST
def verify_flutterwave_payment(request):
    try:
        data = json.loads(request.body)
        reference = data.get("reference", "")  # FLW-TEST-...
        transaction_id = data.get("transaction_id")
        tx_ref = data.get("tx_ref")
        items = data.get("items", [])
        is_test = data.get("is_test", False)

        if not items:
            return JsonResponse({"status": False, "message": "No fees selected"})

        school = request.user.school
        if reference and not reference.startswith("FLW-TEST") and is_test:
            # block fake is_test flag from frontend
            is_test = False

        # === FAKE / TEST MODE - INSTANT SUCCESS (same as Hubtel) ===
        if (
            is_test
            or (reference and reference.startswith("FLW-TEST"))
            or not transaction_id
        ):
            amount_ghs = sum(Decimal(str(i["amount"])) for i in items)
            first_item = items[0]
            fee = get_object_or_404(StudentFee, id=first_item["fee_id"], school=school)
            student = fee.student

            if request.user.role == "student" and student != request.user:
                return JsonResponse({"status": False, "message": "Not your fee"})
            if request.user.role == "parent":
                if not ParentStudentLink.objects.filter(
                    parent=request.user, student=student, school=school
                ).exists():
                    return JsonResponse({"status": False, "message": "Not your child"})
            for item in items:
                dyn_check = get_object_or_404(
                    DynamicStudentFeeItem, id=item["dynamic_item_id"], school=school
                )
                if dyn_check.student != student:
                    return JsonResponse(
                        {"status": False, "message": "Student mismatch"}
                    )
            ay_obj, _ = AcademicYear.objects.get_or_create(
                name=fee.academic_year, school=school, defaults={"is_active": False}
            )

            with transaction.atomic():
                txn = PaymentTransaction.objects.create(
                    student=fee.student,
                    school=school,
                    total_amount=amount_ghs,
                    amount_paid=amount_ghs,
                    term=fee.term,
                    academic_year=ay_obj,
                    payment_method="flutterwave",
                    transaction_id=reference,
                    recorded_by=request.user,
                )
                for item in items:
                    dyn_id = item.get("dynamic_item_id")
                    item_amount = Decimal(str(item["amount"]))
                    fee_name = item.get("fee_name")
                    dyn_fee_item = get_object_or_404(
                        DynamicStudentFeeItem,
                        id=dyn_id,
                        school=school,
                        student=fee.student,
                    )
                    dyn_fee_item.amount_paid = (
                        dyn_fee_item.amount_paid or 0
                    ) + item_amount
                    dyn_fee_item.save()
                    PaymentItem.objects.create(
                        transaction=txn,
                        payment_type=dyn_fee_item.payment_type,
                        fee_name=fee_name,
                        amount=item_amount,
                    )
                FeeAuditLog.objects.create(
                    transaction=txn,
                    action="COLLECTED",
                    done_by=request.user,
                    details=f"FLUTTERWAVE TEST - GHS {amount_ghs} - Ref {reference} - {fee.student.get_full_name()} - {', '.join([i['fee_name'] for i in items])}",
                )

            total_bal = sum(
                [
                    i.balance
                    for i in DynamicStudentFeeItem.objects.filter(
                        student=fee.student, school=school, student_fee__term=fee.term
                    )
                ]
            )
            return JsonResponse(
                {
                    "status": True,
                    "receipt": txn.receipt_number,
                    "balance": float(total_bal),
                }
            )

        # === REAL MODE ===
        if not school.flutterwave_secret_key:
            return JsonResponse(
                {"status": False, "message": "School payment not configured"}
            )

        headers = {"Authorization": f"Bearer {school.flutterwave_secret_key}"}
        url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
        resp = requests.get(url, headers=headers, timeout=15).json()

        if resp.get("status") == "success" and resp["data"]["status"] == "successful":
            amount_ghs = Decimal(str(resp["data"]["amount"]))
            first_item = items[0]
            fee = get_object_or_404(StudentFee, id=first_item["fee_id"], school=school)
            student = fee.student
            if request.user.role == "student" and student != request.user:
                return JsonResponse({"status": False, "message": "Not your fee"})
            if request.user.role == "parent":
                if not ParentStudentLink.objects.filter(
                    parent=request.user, student=student, school=school
                ).exists():
                    return JsonResponse({"status": False, "message": "Not your child"})
            for item in items:
                dyn_check = get_object_or_404(
                    DynamicStudentFeeItem, id=item["dynamic_item_id"], school=school
                )
                if dyn_check.student != student:
                    return JsonResponse(
                        {"status": False, "message": "Student mismatch"}
                    )
            ay_obj, _ = AcademicYear.objects.get_or_create(
                name=fee.academic_year, school=school, defaults={"is_active": False}
            )
            with transaction.atomic():
                txn = PaymentTransaction.objects.create(
                    student=fee.student,
                    school=school,
                    total_amount=amount_ghs,
                    amount_paid=amount_ghs,
                    term=fee.term,
                    academic_year=ay_obj,
                    payment_method="flutterwave",
                    transaction_id=tx_ref or str(transaction_id),
                    recorded_by=request.user,
                )
                for item in items:
                    dyn_id = item.get("dynamic_item_id")
                    item_amount = Decimal(str(item["amount"]))
                    fee_name = item.get("fee_name")
                    dyn_fee_item = get_object_or_404(
                        DynamicStudentFeeItem,
                        id=dyn_id,
                        school=school,
                        student=fee.student,
                    )
                    dyn_fee_item.amount_paid = (
                        dyn_fee_item.amount_paid or 0
                    ) + item_amount
                    dyn_fee_item.save()
                    PaymentItem.objects.create(
                        transaction=txn,
                        payment_type=dyn_fee_item.payment_type,
                        fee_name=fee_name,
                        amount=item_amount,
                    )
                FeeAuditLog.objects.create(
                    transaction=txn,
                    action="COLLECTED",
                    done_by=request.user,
                    details=f"FLUTTERWAVE - GHS {amount_ghs} - Ref {tx_ref} - {fee.student.get_full_name()}",
                )

            total_bal = sum(
                [
                    i.balance
                    for i in DynamicStudentFeeItem.objects.filter(
                        student=fee.student, school=school, student_fee__term=fee.term
                    )
                ]
            )
            return JsonResponse(
                {
                    "status": True,
                    "receipt": txn.receipt_number,
                    "balance": float(total_bal),
                }
            )
        else:
            return JsonResponse(
                {"status": False, "message": "Flutterwave verification failed"}
            )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return JsonResponse({"status": False, "message": str(e)}, status=500)


@login_required
def fee_history(request, fee_id):
    school = request.user.school
    if not school:
        messages.error(request, "No school assigned")
        return redirect('accounts:dashboard')

    # Permission: admin + headmaster only (admission view)
    if request.user.role not in [
        "admin",
        "proprietor",
        "proprietress",
        "headmaster",
        "headmistress",
        "accountant",
        "bursar",
    ]:
        messages.error(request, "Access denied - admin/headmaster only")
        return redirect('accounts:dashboard')

    fee = get_object_or_404(StudentFee, id=fee_id, school=school, student__school=school)

    # FIXED: filter by school, use .filter().first() to avoid crash
    academic_year_obj = AcademicYear.objects.filter(
        name=fee.academic_year, 
        school=school
    ).first()

    if academic_year_obj:
        transactions = PaymentTransaction.objects.filter(
            student=fee.student,
            term=fee.term,
            academic_year=academic_year_obj,
            school=school
        ).prefetch_related('items').order_by('-created_at')
    else:
        # Fallback if AcademicYear object not created yet - show by term only
        transactions = PaymentTransaction.objects.filter(
            student=fee.student,
            term=fee.term,
            school=school
        ).prefetch_related('items').order_by('-created_at')

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

    # SCHOOL ISOLATED
    years = AcademicYear.objects.filter(school=student.school).order_by('-name')
    terms = Term.objects.filter(school=student.school).order_by('-start_date')

    year_id = request.GET.get('year') or ""
    term_id = request.GET.get('term') or ""
    year_id = year_id if year_id != "" else None
    term_id = term_id if term_id != "" else None

    # APPROVED PAYMENTS
    payments_qs = PaymentTransaction.objects.filter(
        student=student,
        school=student.school,
        is_voided=False
    ).select_related('term', 'academic_year', 'recorded_by')

    # PENDING PAYMENTS - THIS WAS THE PROBLEM BEFORE
    pending_base_qs = PendingPayment.objects.filter(
        student=student,
        school=student.school
    ).select_related('term', 'academic_year', 'student_fee')

    if year_id:
        payments_qs = payments_qs.filter(academic_year_id=year_id)
        pending_base_qs = pending_base_qs.filter(academic_year_id=year_id)
    if term_id:
        payments_qs = payments_qs.filter(term_id=term_id)
        pending_base_qs = pending_base_qs.filter(term_id=term_id)

    payments_all = payments_qs.order_by('-created_at')
    total_amount = payments_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0

    # PAGINATOR 10 per page
    paginator = Paginator(payments_all, 10)
    page_number = request.GET.get('page')
    payments = paginator.get_page(page_number)

    all_pending = pending_base_qs.order_by('-submitted_at')
    pending_payments = all_pending.filter(status='pending')
    rejected_payments = all_pending.filter(status='rejected')

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
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
        return redirect("accounts:dashboard")

    school = request.user.school
    parent = get_object_or_404(User, id=parent_id, role="parent", school=school)

    classes = SchoolClass.objects.filter(school=school).order_by("name")
    selected_class_id = request.GET.get("class_id")
    search_q = request.GET.get("q", "")

    students = User.objects.filter(role="student", school=school, is_active=True)

    if selected_class_id:
        students = students.filter(school_class_id=selected_class_id)
    if search_q:
        students = students.filter(
            models.Q(first_name__icontains=search_q)
            | models.Q(last_name__icontains=search_q)
            | models.Q(student_number__icontains=search_q)
        )

    students = students.order_by("last_name")
    paginator = Paginator(students, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    if request.method == "POST":
        selected_class_id_post = request.POST.get("selected_class_id")
        selected_students = request.POST.getlist("students")

        if selected_class_id_post:
            class_student_ids = User.objects.filter(
                school_class_id=selected_class_id_post, school=school, role="student"
            ).values_list("id", flat=True)
            ParentStudentLink.objects.filter(parent=parent, student_id__in=class_student_ids, student__school=school).delete()
        else:
            ParentStudentLink.objects.filter(parent=parent, student__school=school).delete()

        for student_id in selected_students:
            rel = request.POST.get(f"relationship_{student_id}") or None
            student = User.objects.get(id=student_id, role="student", school=school)
            obj, is_new = ParentStudentLink.objects.get_or_create(
                parent=parent, student=student,
                defaults={"relationship": rel, "school": school}
            )
            obj.relationship = rel
            obj.school = school
            obj.save()

        messages.success(request, "Students linked successfully!")
        if selected_class_id_post:
            return redirect(f"{request.path}?class_id={selected_class_id_post}")
        return redirect("accounts:manage-parents")

    linked_map = dict(ParentStudentLink.objects.filter(parent=parent, student__school=school).values_list("student_id", "relationship"))
    for s in page_obj:
        s.link_relationship = linked_map.get(s.id, "")

    return render(request, "accounts/link_students_to_parent.html", {
        "parent": parent, "students": page_obj, "classes": classes,
        "selected_class_id": selected_class_id, "search_q": search_q, "total_linked": len(linked_map),
    })


@login_required
def my_children(request):
    if request.user.role != 'parent':
        return redirect('accounts:parent_dashboard')

    school = request.user.school
    children = ParentStudentLink.objects.filter(
        parent=request.user,
        student__school=school,  
        student__is_active=True
    ).select_related('student', 'student__school_class', 'student__school')

    return render(request, 'accounts/my_children.html', {
        'children': children,
        'child_count': children.count(),
    })


@login_required
def parent_payment_history(request):
    if request.user.role!= 'parent':
        return redirect('accounts:dashboard')
    school = request.user.school # ISOLATION

    # 1. SCHOOL ISOLATED children
    links = ParentStudentLink.objects.filter(
        parent=request.user,
        student__school=school # <-- FIX
    ).select_related('student')
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
        if not selected_child and children:
            selected_child = children[0]

    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    # 2. APPROVED payments — already isolated by school=school (good)
    if selected_child:
        payments_qs = PaymentTransaction.objects.filter(
            school=school,
            student=selected_child,
            is_voided=False
        ).select_related('student', 'term', 'academic_year').order_by('-created_at')
    else:
        children_ids = [c.id for c in children]
        payments_qs = PaymentTransaction.objects.filter(
            school=school,
            student__id__in=children_ids,
            is_voided=False
        ).select_related('student', 'term', 'academic_year').order_by('-created_at')

    # 3. PENDING — SCHOOL ISOLATED FIX
    if selected_child:
        base_pending_qs = PendingPayment.objects.filter(student=selected_child, school=school)
    else:
        base_pending_qs = PendingPayment.objects.filter(student__in=children, school=school)

    all_pending_records = base_pending_qs.select_related('student', 'term', 'academic_year').order_by('-submitted_at')
    pending_payments_qs = all_pending_records.filter(status='pending')
    rejected_payments_qs = all_pending_records.filter(status='rejected')

    # Filters
    academic_year_id = request.GET.get("academic_year")
    term_id = request.GET.get("term")
    if academic_year_id:
        payments_qs = payments_qs.filter(academic_year_id=academic_year_id)
        pending_payments_qs = pending_payments_qs.filter(academic_year_id=academic_year_id)
        rejected_payments_qs = rejected_payments_qs.filter(academic_year_id=academic_year_id)
    if term_id:
        payments_qs = payments_qs.filter(term_id=term_id)
        pending_payments_qs = pending_payments_qs.filter(term_id=term_id)
        rejected_payments_qs = rejected_payments_qs.filter(term_id=term_id)

    # 4. PAGINATOR — 10 per page, with View All
    view_all = request.GET.get("view") == "all"
    total_count = payments_qs.count()

    if view_all:
        payments = payments_qs
        page_obj = None
    else:
        paginator = Paginator(payments_qs, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        payments = page_obj

    total_amount = payments_qs.aggregate(total=Sum('total_amount'))['total'] or 0
    pending_count = pending_payments_qs.count()
    pending_amount = pending_payments_qs.aggregate(total=Sum('amount'))['total'] or 0

    academic_years = AcademicYear.objects.filter(school=school).order_by("-name")
    terms = Term.objects.filter(school=school).select_related("academic_year").order_by("-academic_year__name", "term_number")

    return render(request, 'accounts/parent_payment_history.html', {
        "academic_years": academic_years,
        "terms": terms,
        'payments': payments,
        'page_obj': page_obj,
        'show_all': view_all,
        'total_count': total_count,
        'pending_payments': pending_payments_qs,
        'rejected_payments': rejected_payments_qs,
        'pending_count': pending_count,
        'pending_amount': pending_amount,
        'total_amount': total_amount,
        'children': children,
        'selected_child': selected_child,
        'show_switcher': len(children) > 1,
        'school': school,
    })


@login_required
def student_terminal_report(request):
    if request.user.role != 'student':
        return redirect('accounts:dashboard')

    student = request.user
    school = student.school
    years = AcademicYear.objects.filter(school=school).order_by('-name')

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
            grading = GradingScale.objects.filter(
                school=school,
                term=selected_term,
                min_score__lte=r.calc_total,
                max_score__gte=r.calc_total,
            ).first()

            if not grading:
                grading = GradingScale.objects.filter(
                    school=school,
                    term__isnull=True,
                    min_score__lte=r.calc_total,
                    max_score__gte=r.calc_total,
                ).first()

            r.calc_grade = grading.grade if grading else "-"
            r.calc_remark = grading.remark if grading else "-"

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
            grading = GradingScale.objects.filter(
                school=school,
                term=selected_term,
                min_score__lte=percentage,
                max_score__gte=percentage,
            ).first()

            if not grading:
                grading = GradingScale.objects.filter(
                    school=school,
                    term__isnull=True,
                    min_score__lte=percentage,
                    max_score__gte=percentage,
                ).first()

            if grading:
                overall_grade = grading.grade
                overall_remark = grading.remark
            else:
                overall_grade = '-'
                overall_remark = '-'
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
        'years': years,
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
    
    school = request.user.school

    if request.user.role == 'parent':
        student_fee = get_object_or_404(StudentFee, id=student_fee_id, school=school)
        # Check link WITH school
        is_linked = ParentStudentLink.objects.filter(
            parent=request.user, 
            student=student_fee.student,
            student__school=school
        ).exists()
        if not is_linked:
            messages.error(request, "Not authorized.")
            return redirect('accounts:parent_dashboard')
    else:
        student_fee = get_object_or_404(StudentFee, id=student_fee_id, school=school, student=request.user)

    # ===== SAFE + SCHOOL ISOLATED CHECK =====
    if request.user.role == 'parent':
        # Check link without school (your table has school=NULL)
        is_linked = ParentStudentLink.objects.filter(
            parent=request.user, 
            student=student_fee.student
        ).exists()
        
        if not is_linked:
            # Also try parent__children if you use different relation
            try:
                is_linked = ParentStudentLink.objects.filter(
                    parent=request.user, 
                    student_id=student_fee.student.id
                ).exists()
            except:
                pass
        
        if not is_linked:
            messages.error(request, "Not authorized for this student.")
            return redirect('accounts:parent_dashboard')
            
    elif request.user.role == 'student':
        if student_fee.student != request.user:
            messages.error(request, "You can only pay your own fees.")
            return redirect('accounts:view-student-fees')

    # Term handling
    term = student_fee.term
    academic_year = AcademicYear.objects.filter(name=student_fee.academic_year, school=school).first() or (term.academic_year if hasattr(term, 'academic_year') else None)
    if not academic_year:
        try:
            academic_year = AcademicYear.objects.get(name=student_fee.academic_year, school=school)
        except:
            messages.error(request, "Academic year not found.")
            return redirect('accounts:parent_dashboard')

    rejected_payment = None
    resubmit_id = request.GET.get('resubmit')
    if resubmit_id:
        rejected_payment = PendingPayment.objects.filter(id=resubmit_id, student_fee=student_fee, school=school, status='rejected').first()

    if request.method == 'POST':
        form = PendingPaymentForm(request.POST, request.FILES, instance=rejected_payment, school=school) if rejected_payment else PendingPaymentForm(request.POST, request.FILES, school=school)
        if form.is_valid():
            pending = form.save(commit=False)
            pending.school = school
            pending.student = student_fee.student
            pending.student_fee = student_fee
            pending.term = term
            pending.academic_year = academic_year
            pending.submitted_by = request.user
            pending.status = 'pending'
            pending.rejection_reason = None
            pending.save()

            pending.items.all().delete()
            items = json.loads(request.POST.get("selected_items", "[]"))
            total = Decimal('0')
            for item in items:
                PendingPaymentItem.objects.create(
                    pending_payment=pending,
                    fee_name=item["fee_name"],
                    paid_field=item.get("paid_field", item["fee_name"]),
                    amount=Decimal(item["amount"]),
                    dynamic_item_id=item.get("dynamic_item_id")
                )
                total += Decimal(item["amount"])
            pending.amount = total
            pending.save()

            messages.success(request, "Payment proof submitted. Awaiting verification.")
            return redirect("accounts:view-student-fees" if request.user.role == 'student' else "accounts:parent_payment_history")
    else:
        form = PendingPaymentForm(instance=rejected_payment, school=school) if rejected_payment else PendingPaymentForm(school=school)
        if rejected_payment:
            messages.info(request, f"Resubmitting rejected payment. Reason: {rejected_payment.rejection_reason}")

    # DYNAMIC - from DynamicStudentFeeItem only
    dynamic_items = DynamicStudentFeeItem.objects.filter(
        school=school,
        student_fee=student_fee
    )

    payment_options = []
    for item in dynamic_items:
        balance = float(item.amount_due) - float(item.amount_paid)
        if balance > 0.01:
            payment_options.append({
                "label": item.payment_type.name if hasattr(item, 'payment_type') and item.payment_type else item.name,
                "paid_field": item.name,
                "balance": balance,
                "due": float(item.amount_due),
                "paid": float(item.amount_paid),
                "dynamic_item_id": item.id,  
            })

    total_due = sum(float(i.amount_due) for i in dynamic_items) or float(student_fee.total_amount)
    total_paid = sum(float(i.amount_paid) for i in dynamic_items)
    real_balance = total_due - total_paid
    if real_balance < 0:
        real_balance = 0

    context = {
        'form': form,
        'student_fee': student_fee,
        'student': student_fee.student,
        'balance': real_balance,
        'school': school,
        'payment_options': payment_options, 
        'is_resubmitting': rejected_payment is not None,
    }
    return render(request, 'accounts/submit_manual_payment.html', context)


@login_required
def pending_payments(request):
    if request.user.role != 'accountant':
        messages.error(request, "You are not authorized to view this page.")
        return redirect('accounts:dashboard')

    school = request.user.school

    pending_payments = PendingPayment.objects.filter(
        student__school=school,
        school=school,  
        status='pending'
    ).select_related(
        'student',
        'student_fee',
        'term',
        'academic_year',
        'submitted_by'
    ).prefetch_related(
        'items'  
    ).order_by('-submitted_at')

    context = {
        'pending_payments': pending_payments,
        'school': school,
    }
    return render(request, 'accounts/pending_payments.html', context)


@login_required
def pending_payment_detail(request, payment_id):

    if request.user.role != 'accountant':
        messages.error(request, "You are not authorized to view this page.")
        return redirect('accounts:dashboard')

    school = request.user.school

    payment = get_object_or_404(
        PendingPayment.objects.select_related(
            'student',
            'student_fee',
            'term',
            'academic_year',
            'submitted_by',
            'school'
        ).prefetch_related('items'),
        id=payment_id,
        school=school, 
        student__school=school  
    )

    context = {
        'payment': payment,
        'payment_items': payment.items.all(), 
        'school': school,
    }

    return render(request, 'accounts/pending_payment_detail.html', context)

@login_required
def approve_manual_payment(request, payment_id):
    if request.user.role != "accountant":
        messages.error(request, "Not authorized.")
        return redirect("accounts:dashboard")

    school = request.user.school
    payment = get_object_or_404(
        PendingPayment.objects.prefetch_related("items"),
        id=payment_id,
        school=school,  
        student__school=school,
        status="pending",
    )

    if request.method != "POST":
        return redirect("accounts:pending_payment_detail", payment_id=payment.id)

    try:
        with transaction.atomic():
            fee = payment.student_fee

            txn = PaymentTransaction.objects.create(
                school=school,  # ISOLATION
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
                # 1. Create PaymentItem
                PaymentItem.objects.create(
                    transaction=txn,
                    fee_name=item.fee_name,
                    amount=item.amount,
                )

                # 2. DYNAMIC UPDATE - Update DynamicStudentFeeItem, not StudentFee field
                dyn_item, created = DynamicStudentFeeItem.objects.get_or_create(
                    school=school,
                    student_fee=fee,
                    student=payment.student,
                    name=item.fee_name,
                    defaults={"amount_due": item.amount, "amount_paid": Decimal("0")},
                )
                dyn_item.amount_paid = (
                    dyn_item.amount_paid or Decimal("0")
                ) + item.amount
                dyn_item.save()

                # 3. PaymentType with school isolation
                code = item.fee_name.upper().replace(" ", "_")[:50]
                payment_type, _ = PaymentType.objects.get_or_create(
                    school=school, code=code, defaults={"name": item.fee_name}
                )
                txn.payment_types.add(payment_type)

            # Update StudentFee total paid cache if you have
            # fee.amount_paid = sum of dyn items - optional

            payment.status = "approved"
            payment.verified_by = request.user
            payment.verified_at = timezone.now()
            payment.payment_transaction = txn
            payment.save()

            FeeAuditLog.objects.create(
                transaction=txn,
                action="COLLECTED",
                done_by=request.user,
                details=f"ONLINE Approved - {payment.payment_method} {payment.reference_number} - GHS {payment.amount} for {payment.student.get_full_name()} | {school.name}",
            )

            messages.success(
                request, f"Payment approved. Receipt: {txn.receipt_number}"
            )
            return redirect("accounts:pending_payments")

    except Exception as e:
        messages.error(request, f"Error approving: {e}")

    return redirect("accounts:pending_payment_detail", payment_id=payment.id)


@login_required
def reject_manual_payment(request, payment_id):
    if request.user.role != "accountant":
        messages.error(request, "Not authorized.")
        return redirect("accounts:dashboard")

    school = request.user.school
    payment = get_object_or_404(
        PendingPayment,
        id=payment_id,
        school=school,
        student__school=school,
        status="pending",
    )

    if request.method == "POST":
        reason = request.POST.get("rejection_reason", "").strip()
        if not reason:
            messages.error(request, "Please provide rejection reason.")
            return redirect("accounts:pending_payment_detail", payment_id=payment.id)

        payment.status = "rejected"
        payment.rejection_reason = reason
        payment.verified_by = request.user
        payment.verified_at = timezone.now()
        payment.save()

        messages.success(request, "Payment rejected.")
        return redirect("accounts:pending_payments")

    return redirect("accounts:pending_payment_detail", payment_id=payment.id)


@login_required
def parent_timetable(request):
    if request.user.role != "parent":
        return redirect("accounts:dashboard")
    school = request.user.school
    if not school:
        messages.error(request, "No school assigned.")
        return redirect("accounts:parent_dashboard")

    links = ParentStudentLink.objects.filter(
        parent=request.user, student__school=school, student__is_active=True
    ).select_related("student", "student__school_class")
    children = [link.student for link in links]
    selected_child = None
    child_id = request.GET.get("child") or request.session.get(
        "parent_timetable_child_id"
    )
    if child_id:
        for child in children:
            if str(child.id) == str(child_id):
                selected_child = child
                break
    if not selected_child and children:
        selected_child = children[0]
    if selected_child:
        request.session["parent_timetable_child_id"] = selected_child.id

    filter_data = get_term_year_filter(request, school)
    years = filter_data["years"]
    selected_year = request.GET.get("year") or filter_data.get("selected_year")
    selected_term = request.GET.get("term") or filter_data.get("selected_term")
    sy = str(selected_year) if selected_year else ""
    st = str(selected_term) if selected_term else ""

    try:
        timetable_week = int(request.GET.get("timetable_week", "0") or 0)
    except:
        timetable_week = 0

    today = timezone.localdate()
    start_of_week = (
        today - timedelta(days=today.weekday()) + timedelta(weeks=timetable_week)
    )
    end_of_week = start_of_week + timedelta(days=4)

    timetable_grid = defaultdict(dict)
    time_slots = set()
    break_slots = []
    break_times = []
    timetable_by_day = []
    has_timetable = False
    active_term = None
    current_week_number = 1
    weekdays = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
    ]

    if selected_child and selected_child.school_class:
        student_class = selected_child.school_class
        if sy and st:
            active_term = Term.objects.filter(
                school=school, academic_year_id=sy, term_number=st
            ).first()
        else:
            active_term = Term.objects.filter(school=school, is_active=True).first()

        if active_term:
            term_start = active_term.start_date
            first_monday = term_start - timedelta(days=term_start.weekday())
            current_week_number = ((start_of_week - first_monday).days // 7) + 1

        # >>> FIXED: MUST FILTER BY TERM <<<
        if not active_term:
            filtered_timetable = Timetable.objects.none()
        else:
            filtered_timetable = (
                Timetable.objects.filter(
                    school_class=student_class,
                    school_class__school=school,
                    term=active_term,
                )
                .select_related("teacher", "subject", "school_class")
                .order_by("weekday", "start_time")
            )

        for item in filtered_timetable:
            time_range = f"{item.start_time.strftime('%H:%M')} - {item.end_time.strftime('%H:%M')}"
            time_slots.add(time_range)
            timetable_grid[item.weekday][time_range] = item

        breaks = Break.objects.filter(
            school=school, stage=student_class.stage
        ).order_by("start_time")
        for br in breaks:
            break_range = (
                f"{br.start_time.strftime('%H:%M')} - {br.end_time.strftime('%H:%M')}"
            )
            time_slots.add(break_range)
            break_slots.append(
                {
                    "id": br.id,
                    "stage": br.stage,
                    "name": br.name,
                    "start": br.start_time,
                    "end": br.end_time,
                    "label": break_range,
                }
            )
            break_times.append(break_range)

        time_slots = sorted(time_slots)
        for day_value, _ in weekdays:
            if day_value not in timetable_grid:
                timetable_grid[day_value] = {}
        has_timetable = filtered_timetable.exists()

        for day_value, day_name in weekdays:
            current_date = start_of_week + timedelta(days=day_value)
            event = AcademicCalendar.objects.filter(
                school=school, start_date__lte=current_date, end_date__gte=current_date
            ).first()
            timetable_by_day.append(
                {
                    "num": day_value,
                    "name": day_name,
                    "date": current_date,
                    "event": event,
                    "is_today": (current_date == today and timetable_week == 0),
                }
            )

    timetable_total_weeks = 1
    if active_term:
        cur = active_term.start_date
        total = 0
        while cur <= active_term.end_date:
            total += 1
            friday = cur + timedelta(days=(4 - cur.weekday()))
            cur = friday + timedelta(days=3)
        timetable_total_weeks = total or 1

    context = {
        **filter_data,
        "years": years,
        "selected_year": sy,
        "selected_term": st,
        "children": children,
        "selected_child": selected_child,
        "show_switcher": len(children) > 1,
        "timetable_grid": timetable_grid,
        "time_slots": time_slots,
        "break_slots": break_slots,
        "break_times": break_times,
        "weekdays": weekdays,
        "week_dates": {
            day_value: start_of_week + timedelta(days=day_value)
            for day_value, _ in weekdays
        },
        "has_timetable": has_timetable,
        "timetable_by_day": timetable_by_day,
        "timetable_week": timetable_week,
        "prev_timetable_week": timetable_week - 1,
        "next_timetable_week": timetable_week + 1,
        "current_week_number": current_week_number,
        "current_week_start": start_of_week,
        "current_week_end": end_of_week,
        "today": today,
        "today_weekday": today.weekday() if timetable_week == 0 else -1,
        "active_term": active_term,
        "timetable_total_weeks": timetable_total_weeks,
    }
    return render(request, "accounts/parent_timetable.html", context)


@login_required
def parent_attendance(request):
    if request.user.role!= 'parent':
        messages.error(request, "Only parents can view this page.")
        return redirect('accounts:dashboard')

    school = request.user.school # ISOLATION — Source of truth
    if not school:
        messages.error(request, "School not set for your account.")
        return redirect('accounts:dashboard')

    # 1. SCHOOL ISOLATED children
    links = ParentStudentLink.objects.filter(
        parent=request.user,
        student__school=school # <-- FIX
    ).select_related('student')
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
        if not selected_child and children:
            selected_child = children[0]

    if selected_child:
        request.session['selected_child_id'] = selected_child.id

    if not selected_child:
        return render(request, 'accounts/parent_attendance.html', {
            'children': [], 'has_records': False, 'school': school
        })

    # Double check child belongs to parent's school
    if selected_child.school_id!= school.id:
        messages.error(request, "Invalid child selection.")
        return redirect('accounts:parent_attendance')

    setting = SchoolSetting.objects.filter(school=school).first()
    attendance_mode = setting.attendance_mode if setting else 'subject'

    term_id = request.GET.get("term")
    if term_id:
        active_term = Term.objects.filter(id=term_id, school=school).first()
        if not active_term:
            active_term = Term.objects.filter(school=school, is_active=True).first()
    else:
        active_term = Term.objects.filter(school=school, is_active=True).first()
        if not active_term:
            active_term = Term.objects.filter(school=school).order_by('-end_date').first()

    today = active_term.start_date if active_term else timezone.now().date()

    try:
        if "att_week" in request.GET:
            week_offset = int(request.GET.get("att_week", 0))
        else:
            week_offset = None
    except (TypeError, ValueError):
        week_offset = 0

    if active_term and active_term.start_date:

        term_start = active_term.start_date
        term_end = active_term.end_date

        # Determine the current active week when page opens
        if week_offset is None:

            current_date = timezone.now().date()

            first_week_end = term_start + timedelta(
                days=4 - term_start.weekday()
            )

            if current_date <= first_week_end:
                week_offset = 0

            else:
                first_monday = first_week_end + timedelta(days=3)

                if current_date < first_monday:
                    week_offset = 0
                else:
                    week_offset = 1 + (
                        (current_date - first_monday).days // 7
                    )

        # ==========================================
        # WEEK 1 — STARTS ON TERM START DATE
        # ==========================================
        if term_start.weekday() != 0:

            first_week_start = term_start
            first_week_end = term_start + timedelta(
                days=4 - term_start.weekday()
            )

            if week_offset == 0:
                target_monday = first_week_start
                target_friday = min(first_week_end, term_end)

            else:
                # Week 2 starts on the following Monday
                first_monday = first_week_end + timedelta(days=3)

                target_monday = first_monday + timedelta(
                    weeks=week_offset - 1
                )

                target_friday = min(
                    target_monday + timedelta(days=4),
                    term_end
                )

        # ==========================================
        # TERM STARTS ON MONDAY
        # ==========================================
        else:

            target_monday = term_start + timedelta(
                weeks=week_offset
            )

            target_friday = min(
                target_monday + timedelta(days=4),
                term_end
            )

    else:

        current_monday = today - timedelta(days=today.weekday())

        target_monday = current_monday + timedelta(
            weeks=week_offset
        )

        target_friday = target_monday + timedelta(days=4)

    if active_term and active_term.start_date:

        term_start = active_term.start_date

        if term_start.weekday() == 0:
            # Term starts Monday
            att_active_week = week_offset + 1

        else:
            # First week is the partial week
            # from the term start date to Friday
            att_active_week = week_offset + 1

    else:
        att_active_week = 1

    att_prev = week_offset - 1 if week_offset > 0 else None

    # ATTENDANCE QUERY — SCHOOL ISOLATED
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
            final_status = 'P' if 'P' in statuses else 'A'
            records_list.append({'session__date': date, 'status': final_status})

        records = sorted(records_list, key=lambda x: x['session__date'], reverse=True)
        total_count = len(records)
        present_count = len([r for r in records if r['status'] == 'P'])
        absent_count = len([r for r in records if r['status'] == 'A'])
        late_count = 0
        by_subject = []
        has_records = total_count > 0
    else:
        records_qs = AttendanceRecord.objects.filter(
            student=selected_child,
            session__school_class__school=school, # isolation
            session__date__gte=target_monday,
            session__date__lte=target_friday
        ).select_related('session__subject', 'session__teacher').order_by('-session__date')

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

        by_date_overall = defaultdict(list)
        for r in records_qs:
            by_date_overall[r.session.date].append(r.status)

        total_count = len(by_date_overall)
        present_count = sum(1 for s in by_date_overall.values() if 'P' in s)
        absent_count = total_count - present_count
        late_count = 0
        has_records = records_qs.exists()
        records = records_qs

    if active_term and active_term.start_date and active_term.end_date:

        term_start = active_term.start_date
        term_end = active_term.end_date

        # Week 1: term start date → Friday
        first_week_end = term_start + timedelta(
            days=4 - term_start.weekday()
        )

        if term_end <= first_week_end:
            # Entire term fits inside Week 1
            last_attendance_week = 1

        else:
            # Remaining weeks start on Monday
            first_monday = first_week_end + timedelta(days=3)

            remaining_days = (term_end - first_monday).days

            last_attendance_week = 2 + (remaining_days // 7)

    else:
        last_attendance_week = 1

    academic_years = AcademicYear.objects.filter(school=school).order_by("-name")
    terms = Term.objects.filter(school=school).select_related("academic_year").order_by("-academic_year__name", "term_number")

    context = {
        'children': children,
        'selected_child': selected_child,
        'school': school,
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
        user=request.user,
        notification__school=request.user.school
    ).select_related('notification', 'notification__created_by').order_by('-notification__created_at')

    now = timezone.now()
    today = now.date()
    
    if filter_type == 'today':
        notifs = notifs.filter(notification__created_at__date=today)
        
    elif filter_type == 'week':
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)
        notifs = notifs.filter(
            notification__created_at__date__gte=monday,
            notification__created_at__date__lte=friday
        )
        
    elif filter_type == 'term':
        current_term = Term.objects.filter(
            school=request.user.school, 
            is_active=True
        ).first()
        if current_term:
            notifs = notifs.filter(
                notification__created_at__date__gte=current_term.start_date,
                notification__created_at__date__lte=current_term.end_date
            )
    
    elif filter_type == 'year':
        notifs = notifs.filter(notification__created_at__year=today.year)
        
    elif filter_type == 'all':
        pass

    paginator = Paginator(notifs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    grouped = defaultdict(list)
    for n in page_obj:
        created = n.notification.created_at
        date_key = created.strftime('%B %d, %Y') if created else "Recent"
        grouped[date_key].append(n)

    # mark only shown page as read
    NotificationRecipient.objects.filter(
        id__in=[n.id for n in page_obj],
        is_read=False
    ).update(is_read=True)

    return render(request, 'accounts/notifications.html', {
        'grouped_notifications': dict(grouped),
        'page_obj': page_obj,
        'filter': filter_type,
        'total': notifs.count()
    })

@login_required
def get_notification_count(request):
    count = NotificationRecipient.objects.filter(
        user=request.user, 
        is_read=False,
        notification__school=request.user.school 
    ).count()
    return JsonResponse({'count': count})


def _filter_announcements_by_date(queryset, filter_type, school):
    today = timezone.now().date()

    if filter_type == 'today':
        return queryset.filter(created_at__date=today)

    elif filter_type == 'week':
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)
        return queryset.filter(
            created_at__date__gte=monday,
            created_at__date__lte=friday
        )

    elif filter_type == 'term':

        current_term = Term.objects.filter(is_active=True, school=school).first()
        if current_term:
            return queryset.filter(
                created_at__date__gte=current_term.start_date,
                created_at__date__lte=current_term.end_date
            )
        return queryset

    elif filter_type == 'year':
        return queryset.filter(created_at__year=today.year)

    elif filter_type == 'all':
        return queryset  

    return queryset


@login_required
def parent_notifications(request):
    if request.user.role != "parent":
        return redirect("accounts:dashboard")

    filter_type = request.GET.get("filter", "all")
    school = request.user.school

    if not school:
        messages.error(request, "School not assigned to parent.")
        return redirect("accounts:dashboard")

    links = ParentStudentLink.objects.filter(
        parent=request.user, student__school=school
    ).select_related("student", "student__school_class")

    children = [l.student for l in links]

    selected_child = None
    if children:
        child_id = request.GET.get("child")
        if not child_id:
            try:
                child_id = request.session.get("selected_child_id")
            except:
                child_id = None

        for c in children:
            if str(c.id) == str(child_id):
                selected_child = c
                break
        if not selected_child:
            selected_child = children[0]

        try:
            request.session["selected_child_id"] = selected_child.id
        except:
            pass

    all_anns = (
        Announcement.objects.filter(school=school)
        .prefetch_related("target_classes")
        .order_by("-created_at")
    )
    all_anns = _filter_announcements_by_date(all_anns, filter_type, school)

    general_list = []
    child_list = []

    for ann in all_anns:
        roles = [str(r).lower().strip() for r in (ann.target_roles or [])]
        is_for_parent = "parent" in roles or "parents" in roles or "all" in roles
        is_for_student = "student" in roles or "students" in roles
        is_specific = "specific class" in roles

        if not (is_for_parent or is_for_student or is_specific or "all" in roles):
            continue

        has_class_filter = ann.target_class_id or ann.target_classes.exists()

        if not has_class_filter:
            if is_for_parent or is_for_student or is_specific or "all" in roles:
                general_list.append(ann)
        else:
            if selected_child and selected_child.school_class:
                if is_for_parent or is_for_student or is_specific or "all" in roles:
                    old_match = ann.target_class_id == selected_child.school_class_id
                    new_match = ann.target_classes.filter(
                        id=selected_child.school_class_id
                    ).exists()
                    if old_match or new_match:
                        child_list.append(ann)

    filtered = general_list + child_list
    filtered = sorted(filtered, key=lambda x: x.created_at, reverse=True)

    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/parent_notifications.html",
        {
            "children": children,
            "selected_child": selected_child,
            "show_switcher": len(children) > 1,
            "announcements": page_obj,
            "page_obj": page_obj,
            "filter": filter_type,
            "total": len(filtered),
        },
    )


@login_required
def student_notifications(request):
    if request.user.role != "student":
        return redirect("accounts:dashboard")

    filter_type = request.GET.get("filter", "all")
    school = request.user.school

    all_anns = (
        Announcement.objects.filter(school=school)
        .prefetch_related("target_classes")
        .order_by("-created_at")
    )
    all_anns = _filter_announcements_by_date(all_anns, filter_type, school)

    filtered = []
    for ann in all_anns:
        roles = [str(r).lower().strip() for r in (ann.target_roles or [])]
        if not any(
            x in roles
            for x in ["student", "students", "all", "specific class", "specific_class"]
        ):
            continue

        if ann.target_class_id or ann.target_classes.exists():
            if not request.user.school_class:
                continue
            if (
                ann.target_class_id == request.user.school_class_id
                or ann.target_classes.filter(id=request.user.school_class_id).exists()
            ):
                filtered.append(ann)
        else:
            filtered.append(ann)

    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/student_notifications.html",
        {
            "announcements": page_obj,
            "page_obj": page_obj,
            "filter": filter_type,
            "total": len(filtered),
        },
    )


@login_required
def teacher_notifications(request):
    if request.user.role != "teacher":
        return redirect("accounts:dashboard")
    filter_type = request.GET.get("filter", "all")
    all_anns = _filter_announcements_by_date(
        Announcement.objects.filter(school=request.user.school).order_by("-created_at"), 
        filter_type, 
        request.user.school
    )
    filtered = [
        ann
        for ann in all_anns
        if any(
            r in [str(x).lower().strip() for x in (ann.target_roles or [])]
            for r in ["teacher", "teachers", "all"]
        )
    ]

    paginator = Paginator(filtered, 4)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/teacher_notifications.html",
        {
            "announcements": page_obj,
            "page_obj": page_obj,
            "filter": filter_type,
            "total": len(filtered),
        },
    )


@login_required
def accountant_notifications(request):
    if request.user.role != "accountant":
        return redirect("accounts:dashboard")
    filter_type = request.GET.get('filter', 'all')
    
    all_anns = _filter_announcements_by_date(
        Announcement.objects.filter(school=request.user.school).order_by('-created_at'),
        filter_type,
        request.user.school
    )

    filtered = [ann for ann in all_anns if any(r in [str(x).lower().strip() for x in (ann.target_roles or [])] for r in ["accountant","accountants","all"])]

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
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:  
        messages.error(request, "Permission denied.")
        return redirect("accounts:dashboard")

    school = request.user.school
    if not school:
        messages.error(request, "No school assigned.")
        return redirect("accounts:dashboard")

    if request.method == 'POST':
        form = SchoolSmsSettingsForm(request.POST, instance=school)
        if form.is_valid():
            form.save()
            messages.success(request, "SMS Settings saved!")
            return redirect('accounts:school_sms_settings')
    else:
        form = SchoolSmsSettingsForm(instance=school)

    return render(request, 'accounts/school_sms_settings.html', {'form': form, 'school': school})


@login_required
def admin_fee_monitoring(request):
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
        return redirect("accounts:home")

    school = request.user.school
    today = date.today()
    period = request.GET.get("period", "today")
    selected_term_id = request.GET.get("term")
    selected_year_id = request.GET.get("year")

    # Base queries - always school filtered ✅
    base_tx = PaymentTransaction.objects.filter(student__school=school, is_voided=False)
    voided_qs = PaymentTransaction.objects.filter(
        student__school=school, is_voided=True
    )
    expense_qs = Expense.objects.filter(school=school)
    log_qs = FeeAuditLog.objects.filter(done_by__school=school)
    pending_qs = PendingPayment.objects.filter(student__school=school, status="pending")

    all_terms = (
        Term.objects.filter(school=school)
        .select_related("academic_year")
        .order_by("-academic_year__name", "-term_number")
    )
    all_years = AcademicYear.objects.filter(school=school).order_by("-name")
    active_term = all_terms.filter(is_active=True).first()

    # Determine date range
    start_date = None
    end_date = None
    title = ""

    if period == "today":
        base_tx = base_tx.filter(created_at__date=today)
        voided_qs = voided_qs.filter(voided_at__date=today)
        expense_qs = expense_qs.filter(expense_date=today)
        log_qs = log_qs.filter(created_at__date=today)
        title = f"Today - {today}"

    elif period == "week":
        start_date = today - timedelta(days=7)
        base_tx = base_tx.filter(created_at__date__gte=start_date)
        voided_qs = voided_qs.filter(voided_at__date__gte=start_date)
        expense_qs = expense_qs.filter(expense_date__gte=start_date)
        log_qs = log_qs.filter(created_at__date__gte=start_date)
        title = "Last 7 Days"

    elif period == "month":
        base_tx = base_tx.filter(
            created_at__month=today.month, created_at__year=today.year
        )
        voided_qs = voided_qs.filter(
            voided_at__month=today.month, voided_at__year=today.year
        )
        expense_qs = expense_qs.filter(
            expense_date__month=today.month, expense_date__year=today.year
        )
        log_qs = log_qs.filter(
            created_at__month=today.month, created_at__year=today.year
        )
        title = f"This Month - {today.strftime('%B %Y')}"

    elif period == "term":
        if selected_term_id and selected_term_id.isdigit():
            term_obj = all_terms.filter(id=selected_term_id).first()
        else:
            term_obj = active_term

        if term_obj:
            base_tx = base_tx.filter(
                created_at__date__gte=term_obj.start_date,
                created_at__date__lte=term_obj.end_date,
            )
            voided_qs = voided_qs.filter(
                voided_at__date__gte=term_obj.start_date,
                voided_at__date__lte=term_obj.end_date,
            )
            expense_qs = expense_qs.filter(
                expense_date__gte=term_obj.start_date,
                expense_date__lte=term_obj.end_date,
            )
            log_qs = log_qs.filter(
                created_at__date__gte=term_obj.start_date,
                created_at__date__lte=term_obj.end_date,
            )
            title = f"{term_obj.academic_year.name} - {term_obj.get_term_number_display()}"
        else:
            title = "No Active Term"

    elif period == "year":
        if selected_year_id and selected_year_id.isdigit():
            year_obj = all_years.filter(id=selected_year_id).first()
        else:
            year_obj = active_term.academic_year if active_term else all_years.first()

        if year_obj:
            terms_in_year = all_terms.filter(academic_year=year_obj)
            if terms_in_year.exists():
                first = terms_in_year.order_by("start_date").first()
                last = terms_in_year.order_by("-end_date").first()
                base_tx = base_tx.filter(
                    created_at__date__gte=first.start_date,
                    created_at__date__lte=last.end_date,
                )
                voided_qs = voided_qs.filter(
                    voided_at__date__gte=first.start_date,
                    voided_at__date__lte=last.end_date,
                )
                expense_qs = expense_qs.filter(
                    expense_date__gte=first.start_date, expense_date__lte=last.end_date
                )
                log_qs = log_qs.filter(
                    created_at__date__gte=first.start_date,
                    created_at__date__lte=last.end_date,
                )
                title = f"Year - {year_obj.name}"
    else:  # all
        period = "all"
        title = "All Time - Everything Since Started"

    # Aggregations
    today_tx = base_tx
    today_total = today_tx.aggregate(total=Sum("total_amount"))["total"] or 0
    today_count = today_tx.count()

    cash_total = (
        today_tx.filter(payment_method__iexact="cash").aggregate(
            total=Sum("total_amount")
        )["total"]
        or 0
    )
    bank_total = (
        today_tx.exclude(payment_method__iexact="cash").aggregate(
            total=Sum("total_amount")
        )["total"]
        or 0
    )

    by_accountant = (
        today_tx.filter(payment_method__iexact="cash")
        .values(
            "recorded_by__username", "recorded_by__first_name", "recorded_by__last_name"
        )
        .annotate(total=Sum("total_amount"), count=Count("id"))
        .order_by("-total")
    )

    by_online = (
        today_tx.exclude(payment_method__iexact="cash")
        .select_related("student")
        .order_by("-created_at")[:20]
    )

    by_method = today_tx.values("payment_method").annotate(
        total=Sum("total_amount"), count=Count("id")
    )

    voided_today = voided_qs.select_related("student", "voided_by").order_by(
        "-voided_at"
    )[:20]
    expenses_today = expense_qs.order_by("-expense_date")[:20]
    expense_total = expense_qs.aggregate(total=Sum("amount"))["total"] or 0

    cash_in_hand = float(cash_total) - float(expense_total)
    net_balance = float(today_total) - float(expense_total)

    recent_logs = log_qs.select_related("transaction__student", "done_by").order_by(
        "-created_at"
    )[:30]
    pending_count = pending_qs.count()

    context = {
        "today_total": today_total,
        "today_count": today_count,
        "cash_total": cash_total,
        "bank_total": bank_total,
        "by_accountant": by_accountant,
        "by_method": by_method,
        "voided_today": voided_today,
        "expenses_today": expenses_today,
        "expense_total": expense_total,
        "cash_in_hand": cash_in_hand,
        "net_balance": net_balance,
        "recent_logs": recent_logs,
        "pending_count": pending_count,
        "today": title,
        "period": period,
        "by_online": by_online,
        "all_terms": all_terms,
        "all_years": all_years,
        "selected_term_id": selected_term_id,
        "selected_year_id": selected_year_id,
    }
    return render(request, "accounts/admin_fee_monitoring.html", context)


@login_required
def void_transaction(request, txn_id):
    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
        return redirect('accounts:home')

    txn = get_object_or_404(PaymentTransaction, id=txn_id, student__school=request.user.school)

    if txn.is_voided:
        messages.error(request, "Already voided.")
        return redirect('accounts:admin_fee_monitoring')

    if request.method == 'POST':
        reason = request.POST.get('void_reason', '').strip()
        if not reason:
            messages.error(request, "Reason required.")
            return render(request, 'accounts/void_confirm.html', {'txn': txn})
            
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

    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
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
        return redirect("accounts:headmaster_dashboard")

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

    if request.user.role not in [
    'admin',
    'proprietor',
    'proprietress',
    'headmaster',
    'headmistress'
]:
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
    # =========================================================
    # ACTIVE TERM
    # =========================================================
    try:
        active_term = Term.objects.get(
            school=school,
            is_active=True
        )
    except Term.DoesNotExist:
        active_term = None
    # =========================================================
    # YEAR / TERM FILTERS
    # =========================================================
    selected_year = request.GET.get("year")
    selected_term = request.GET.get("term")
    selected_term_obj = None
    # Available academic years for this school
    academic_years = AcademicYear.objects.filter(
        school=school
    ).order_by("-name")
    # Available terms for this school
    terms = Term.objects.filter(
        school=school
    ).select_related(
        "academic_year"
    ).order_by(
        "-academic_year__name",
        "term_number"
    )
    # =========================================================
    # APPLY YEAR / TERM FILTER
    # =========================================================
    if selected_term:
        try:
            selected_term_obj = terms.get(
                id=selected_term
            )
        except Term.DoesNotExist:
            selected_term_obj = None
    # If a year is selected, make sure the selected term
    # belongs to that academic year.
    if selected_year and selected_term_obj:
        if str(selected_term_obj.academic_year_id) != str(selected_year):
            selected_term_obj = None
    # If only a year is selected, use the first available
    # term belonging to that academic year.
    if selected_year and not selected_term_obj:
        year_terms = terms.filter(
            academic_year_id=selected_year
        )
        selected_term_obj = year_terms.first()
    # If a specific term was selected, use its academic year
    # as the selected year.
    if selected_term_obj:
        selected_year = str(
            selected_term_obj.academic_year_id
        )
    # If no filter was selected, continue using the active term.
    if not selected_year and not selected_term:
        selected_term_obj = active_term
        if active_term:
            selected_year = str(
                active_term.academic_year_id
            )
    # =========================================================
    # WEEK SELECTION
    # =========================================================
    week_str = request.GET.get("attendance_week")
    if week_str:
        selected_date = parse_date(week_str)
    else:
        # When looking at a historical term, start from that
        # term's beginning instead of today's date.
        if selected_term_obj and selected_term_obj != active_term:
            selected_date = selected_term_obj.start_date
        else:
            selected_date = timezone.localdate()
    if not selected_date:
        selected_date = timezone.localdate()
    # =========================================================
    # WEEK NUMBER
    # =========================================================
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
    # =========================================================
    # MOVE WEEKENDS TO FRIDAY
    # =========================================================
    if selected_date.weekday() >= 5:
        selected_date = selected_date - timedelta(
            days=selected_date.weekday() - 4
        )
    # =========================================================
    # WEEK RANGE
    # =========================================================
    week_start = selected_date - timedelta(
        days=selected_date.weekday()
    )
    week_end = week_start + timedelta(days=4)
    # =========================================================
    # WEEK NAVIGATION
    # =========================================================
    attendance_previous_week = week_start - timedelta(days=7)
    attendance_next_week = week_start + timedelta(days=7)
    is_first_week = False
    is_last_week = False
    # Use selected term for navigation when filtering
    # historical attendance.
    navigation_term = selected_term_obj or active_term
    if navigation_term:
        first_week_start = navigation_term.start_date - timedelta(
            days=navigation_term.start_date.weekday()
        )
        last_week_start = navigation_term.end_date - timedelta(
            days=navigation_term.end_date.weekday()
        )
        if week_start <= first_week_start:
            is_first_week = True
            previous_week = week_start
        if week_start >= last_week_start:
            is_last_week = True
            next_week = week_start
    # =========================================================
    # CURRENT WEEK
    # =========================================================
    current_week_start = timezone.localdate() - timedelta(
        days=timezone.localdate().weekday()
    )
    is_current_week = week_start == current_week_start
    # =========================================================
    # WORKING DAYS
    # =========================================================
    working_days = []
    current = week_start
    while current <= week_end:
        if current.weekday() < 5:
            working_days.append(current)
        current += timedelta(days=1)
    # =========================================================
    # ATTENDANCE RECORDS
    # =========================================================
    attendance_records = TeacherAttendance.objects.filter(
        teacher=teacher,
        school=school,
        date__range=[week_start, week_end]
    )
    attendance_map = {
        attendance.date: attendance
        for attendance in attendance_records
    }
    # =========================================================
    # ATTENDANCE SUMMARY
    # =========================================================
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
    # =========================================================
    # ATTENDANCE RATE
    # =========================================================
    attendance_rate = 0
    if working_days:
        attendance_rate = round(
            ((present + late) / len(working_days)) * 100,
            1
        )
    # =========================================================
    # ATTENDANCE WEEK NUMBER
    # =========================================================
    attendance_week_number = 1
    if navigation_term:
        attendance_week_number = get_week_number(
            navigation_term.start_date,
            selected_date
        )
    # =========================================================
    # RENDER
    # =========================================================
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
            # =================================================
            # NEW FILTER VARIABLES
            # =================================================
            "academic_years": academic_years,
            "terms": terms,
            "selected_year": selected_year,
            "selected_term": (
                str(selected_term_obj.id)
                if selected_term_obj
                else ""
            ),
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

    # LOCATION CHECK
    if request.method != "POST":
        messages.error(
            request,
            "Please use the Check In button."
        )
        return redirect("accounts:teacher-dashboard")

    latitude = request.POST.get("latitude")
    longitude = request.POST.get("longitude")

    if not latitude or not longitude:
        messages.error(
            request,
            "Your location could not be detected. Please allow location access and try again."
        )
        return redirect("accounts:teacher-dashboard")

    if school.latitude is None or school.longitude is None:
        messages.error(
            request,
            "School attendance location has not been configured."
        )
        return redirect("accounts:teacher-dashboard")

    try:
        teacher_lat = float(latitude)
        teacher_lon = float(longitude)
        school_lat = float(school.latitude)
        school_lon = float(school.longitude)

        # Calculate distance using the Haversine formula
        earth_radius = 6371000  # metres

        lat1 = radians(school_lat)
        lat2 = radians(teacher_lat)
        lat_difference = radians(teacher_lat - school_lat)
        lon_difference = radians(teacher_lon - school_lon)

        a = (
            sin(lat_difference / 2) ** 2
            + cos(lat1)
            * cos(lat2)
            * sin(lon_difference / 2) ** 2
        )

        c = 2 * atan2(sqrt(a), sqrt(1 - a))

        distance = earth_radius * c

    except (ValueError, TypeError):
        messages.error(
            request,
            "Invalid location data. Please try again."
        )
        return redirect("accounts:teacher-dashboard")

    # Teacher must be within the school's allowed radius
    if distance > school.attendance_radius:
        messages.error(
            request,
            "You are outside the school attendance area. Please check in from the school premises."
        )
        return redirect("accounts:teacher-dashboard")

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

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import urllib.parse
from xml.sax.saxutils import escape


@login_required
def print_admission_letter(request, student_id):
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
        return redirect("accounts:home")

    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        Image,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    import os
    from io import BytesIO
   
    student = get_object_or_404(
        User, id=student_id, school=request.user.school, role="student"
    )
    school = student.school
    login_enabled = (
        school.allows_student_login
        and student.school_class
        and student.school_class.allows_student_login
    )

    raw_password = request.GET.get("pwd", "")
    if raw_password:
        raw_password = urllib.parse.unquote(raw_password)
    else:
        raw_password = "***"

    school_name = getattr(school, "name", "") or "My School"
    school_address = getattr(school, "address", "") or ""
    school_gps = getattr(school, "gps_address", "") or ""
    school_email = getattr(school, "email", "") or ""
    school_phone = getattr(school, "phone", "") or ""
    school_logo = getattr(school, "logo", None)
    school_motto = getattr(school, "motto", "") or "Knowledge, Discipline, Service"

    active_term = Term.objects.filter(school=school, is_active=True).first()
    if active_term and active_term.academic_year:
        acad_year = (
            active_term.academic_year.name
            if hasattr(active_term.academic_year, "name")
            else str(active_term.academic_year)
        )
    else:
        active_year_obj = AcademicYear.objects.filter(
            school=school, is_active=True
        ).first()
        acad_year = (
            active_year_obj.name
            if active_year_obj
            else f"{datetime.now().year}/{datetime.now().year+1}"
        )

    gender_val = getattr(student, "gender", "")
    gender_display = (
        student.get_gender_display()
        if hasattr(student, "get_gender_display") and gender_val
        else (gender_val.capitalize() if gender_val else "N/A")
    )

    # === CUSTOM WORDING HELPER ===
    def fill(text):
        if not text:
            return ""
        return (
            text.replace("{school_name}", school_name)
            .replace("{student_name}", student.get_full_name())
            .replace(
                "{class_name}",
                student.school_class.name if student.school_class else "the school",
            )
            .replace("{academic_year}", acad_year)
        )

    custom_title = getattr(school, "admission_letter_title", "") or "ADMISSION LETTER"
    custom_body = getattr(school, "admission_letter_body", "") or ""
    custom_footer = (
        getattr(school, "admission_letter_footer", "")
        or "We look forward to welcoming you to the {school_name} family."
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=55,
        leftMargin=55,
        topMargin=30,
        bottomMargin=40,
    )

    BLUE = colors.HexColor("#1e40af")
    LIGHT_BLUE = colors.HexColor("#dbeafe")
    GOLD = colors.HexColor("#f59e0b")

    story = []

    def add_watermark(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 48)
        canvas.setFillColor(colors.Color(0, 0, 0, alpha=0.04))
        canvas.translate(A4[0] / 2, A4[1] / 2)
        canvas.rotate(40)
        words = school_name.upper().split()
        if len(words) >= 2:
            mid = len(words) // 2
            line1 = " ".join(words[:mid])
            line2 = " ".join(words[mid:])
            canvas.drawCentredString(0, 25, line1)
            canvas.drawCentredString(0, -25, line2)
        else:
            canvas.drawCentredString(0, 0, school_name.upper())
        canvas.restoreState()

    styles_main = ParagraphStyle(
        "main", fontName="Helvetica", fontSize=10, leading=14, alignment=TA_JUSTIFY
    )

    if (
        school_logo
        and hasattr(school_logo, "path")
        and os.path.exists(school_logo.path)
    ):
        try:
            logo = Image(school_logo.path, width=75, height=75)
            header = [
                [
                    logo,
                    Paragraph(
                        f"""
                <font size=15 color="#1e40af"><b>{escape(school_name.upper())}</b></font><br/>
                <font size=8.5 color="#444">{escape(school_address)} | GPS: {escape(school_gps)}<br/>
                Tel: {escape(school_phone)} | Email: {escape(school_email)}<br/>
                <i><b>Motto: {escape(school_motto)}</b></i></font>
            """,
                        ParagraphStyle("h", fontSize=9, leading=11),
                    ),
                ]
            ]
            ht = Table(header, colWidths=[85, 395])
        except:
            ht = Table(
                [
                    [
                        Paragraph(
                            f"<font size=15 color='#1e40af'><b>{escape(school_name.upper())}</b></font>",
                            ParagraphStyle("h2", alignment=TA_CENTER),
                        )
                    ]
                ],
                colWidths=[480],
            )
    else:
        ht = Table(
            [
                [
                    Paragraph(
                        f"<font size=15 color='#1e40af'><b>{escape(school_name.upper())}</b></font><br/><font size=8>{escape(school_address)}</font>",
                        ParagraphStyle("h2", alignment=TA_CENTER),
                    )
                ]
            ],
            colWidths=[480],
        )

    ht.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 2.5, BLUE),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(ht)
    story.append(Spacer(1, 18))

    # TITLE - NOW CUSTOM
    title_style = ParagraphStyle(
        "title",
        fontName="Helvetica-Bold",
        fontSize=17,
        textColor=BLUE,
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    story.append(Paragraph(escape(fill(custom_title)), title_style))

    adm_style = ParagraphStyle(
        "adm",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    adm_box = Table(
        [[Paragraph(f"ADMISSION NO: {escape(student.student_number)}", adm_style)]],
        colWidths=[220],
    )
    adm_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BLUE),
                ("ROUNDEDCORNERS", [6, 6, 6, 6]),
                ("PADDING", (0, 0), (-1, -1), 7),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    adm_wrap = Table([[adm_box]], colWidths=[480])
    adm_wrap.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    story.append(adm_wrap)
    story.append(Spacer(1, 16))

    story.append(
        Paragraph(
            f"Date: <b>{datetime.now().strftime('%B %d, %Y')}</b>",
            ParagraphStyle("date", fontSize=9, alignment=0),
        )
    )
    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            f"Dear <b>{escape(student.get_full_name())}</b>,",
            ParagraphStyle("dear", fontName="Helvetica-Bold", fontSize=11),
        )
    )
    story.append(Spacer(1, 8))

    # BODY - NOW CUSTOM
    if custom_body.strip():
        filled_body = fill(custom_body)
        letter_text = "<br/><br/>".join(escape(p) for p in filled_body.split("\n"))
    else:
        letter_text = f"""
        We are delighted to inform you that following your successful application and interview,
        you have been offered admission to <b>{escape(school_name)}</b> as a pupil of <b>{escape(student.school_class.name) if student.school_class else 'the school'}</b>
        for the <b>{escape(acad_year)}</b> academic year.<br/><br/>
        At {escape(school_name)}, we are committed to providing holistic education...
        """
    story.append(Paragraph(letter_text, styles_main))
    story.append(Spacer(1, 14))

    details = [
        [
            Paragraph("<b>Student Name:</b>", ParagraphStyle("l", fontSize=9)),
            Paragraph(escape(student.get_full_name()), ParagraphStyle("v", fontSize=9)),
        ],
        [
            Paragraph("<b>Student ID:</b>", ParagraphStyle("l", fontSize=9)),
            Paragraph(
                f"<b><font color='#1e40af'>{escape(student.student_number)}</font></b>",
                ParagraphStyle("v", fontSize=9),
            ),
        ],
        [
            Paragraph("<b>Class Admitted:</b>", ParagraphStyle("l", fontSize=9)),
            Paragraph(
                escape(student.school_class.name) if student.school_class else "N/A",
                ParagraphStyle("v", fontSize=9),
            ),
        ],
        [
            Paragraph("<b>Gender:</b>", ParagraphStyle("l", fontSize=9)),
            Paragraph(escape(gender_display), ParagraphStyle("v", fontSize=9)),
        ],
        [
            Paragraph("<b>Academic Year:</b>", ParagraphStyle("l", fontSize=9)),
            Paragraph(escape(acad_year), ParagraphStyle("v", fontSize=9)),
        ],
    ]
    t = Table(details, colWidths=[130, 350])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eff6ff")),
                ("BACKGROUND", (1, 0), (1, -1), colors.white),
                ("TEXTCOLOR", (0, 0), (0, -1), BLUE),
                ("GRID", (0, 0), (-1, -1), 0.6, LIGHT_BLUE),
                ("PADDING", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROUNDEDCORNERS", [8, 8, 8, 8]),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 16))

    if login_enabled:
        story.append(
            Paragraph(
                "<b><font color='#92400e'>Student Portal Login Credentials (Keep Confidential)</font></b>",
                ParagraphStyle("credh", fontSize=10, spaceAfter=6),
            )
        )

        creds = [
            ["Portal URL:", f"{request.get_host()}/login/"],
            ["Username:", escape(student.username)],
            ["Student ID:", escape(student.student_number)],
            ["Temporary Password:", escape(raw_password)],
        ]

        ct = Table(creds, colWidths=[120, 360])
        ct.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),
                    ("BOX", (0, 0), (-1, -1), 1.2, GOLD),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("PADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        story.append(ct)
        story.append(Spacer(1, 20))

    # FOOTER - NOW CUSTOM
    footer_filled = fill(custom_footer)
    footer_html = "<br/>".join(escape(p) for p in footer_filled.split("\n"))
    story.append(
        Paragraph(
            f"{footer_html}<br/><br/><br/>_________________________<br/><b>Headmaster</b><br/>{escape(school_name)}",
            ParagraphStyle("sign", fontSize=10, leading=13),
        )
    )

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    buffer.seek(0)
    from django.http import HttpResponse

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="Admission_{student.student_number}.pdf"'
    )
    return response


@login_required
def student_transcript(request, student_id):
    if request.user.role not in ["admin", "proprietor", "proprietress", "headmaster", "headmistress"]:
        return redirect("accounts:home")
    school = request.user.school
    student = get_object_or_404(User, id=student_id, role="student", school=school)

    setting = SchoolSetting.objects.filter(school=school).first()
    default_ca = setting.class_score_total if setting else 0
    default_exam = setting.exam_score_total if setting else 0

    results = (
        Result.objects.filter(student=student, school=school, status="published")
        .select_related("subject", "term", "academic_year")
        .order_by("academic_year__name", "term__term_number", "subject__name")
    )

    # Use normal dict - no defaultdict needed
    grouped = {}
    for r in results:
        if not r.term:
            continue
        ay = r.academic_year.name if r.academic_year else "N/A"
        term_id = r.term.id
        if ay not in grouped:
            grouped[ay] = {}
        if term_id not in grouped[ay]:
            grouped[ay][term_id] = {"term_obj": r.term, "list": []}
        grouped[ay][term_id]["list"].append(r)

    transcript_data = []
    grand_total = 0
    grand_max = 0

    for ay, terms_dict in grouped.items():
        for term_id, data in terms_dict.items():
            term_obj = data["term_obj"]
            res_list = data["list"]
            ca_total = (
                term_obj.ca_total if term_obj.ca_total is not None else default_ca
            )
            exam_total = (
                term_obj.exam_total if term_obj.exam_total is not None else default_exam
            )
            max_total = ca_total + exam_total
            avg = (
                sum([x.total_score for x in res_list]) / len(res_list)
                if res_list
                else 0
            )
            grand_total += sum([x.total_score for x in res_list])
            grand_max += max_total * len(res_list)



            transcript_data.append(
                {
                    "academic_year": ay,
                    "term": term_obj.get_term_number_display(),
                    "term_obj": term_obj,
                    "ca_total": ca_total,
                    "exam_total": exam_total,
                    "max_total": max_total,
                    "results": res_list,
                    "average": round(avg, 2),
                }
            )
    cumulative_percent = round((grand_total / grand_max * 100) if grand_max else 0, 2)
    first_year = transcript_data[0]["academic_year"] if transcript_data else "N/A"
    last_year = transcript_data[-1]["academic_year"] if transcript_data else "N/A"

    # --- ATTENDANCE FINAL - ELAPSED DAYS ---
    current_term = Term.objects.filter(school=school, is_active=True).first()

    if current_term:

        try:
            total_school_days = calculate_school_days(current_term.start_date, date.today())
        except:
            # fallback: use days_opened if function fails
            # If you want elapsed not total, calculate working days
            total_school_days = current_term.days_opened
            # Better manual: count Mon-Fri from start_date to today
            delta = (date.today() - current_term.start_date).days + 1
            count = 0
            for i in range(delta):
                d = current_term.start_date + timedelta(days=i)
                if d.weekday() < 5: # Mon-Fri
                    count += 1
            total_school_days = count
    else:
        total_school_days = AttendanceSession.objects.filter(school=school).values('date').distinct().count()

    # FIX for 0 present - don't filter by term if term is null in old data
    attendance_qs = AttendanceRecord.objects.filter(student=student, school=school).select_related('session')
    if current_term:
        # Try to get records for this term by date range, not just term FK
        attendance_qs = attendance_qs.filter(
            session__date__gte=current_term.start_date,
            session__date__lte=current_term.end_date
        )

    # Group by date without defaultdict
    date_map = {}
    for rec in attendance_qs.select_related("session"):
        d = rec.session.date
        if d not in date_map:
            date_map[d] = []
        date_map[d].append(rec.status)

    present_dates = set()
    absent_dates = set()
    for d, statuses in date_map.items():
        if "P" in statuses:
            present_dates.add(d)
        elif "A" in statuses:
            absent_dates.add(d)

    days_present = len(present_dates)
    days_absent = len(absent_dates)
    days_marked = len(present_dates | absent_dates)
    attendance_percent = round(
        (days_present / total_school_days * 100) if total_school_days else 0, 1
    )
    attendance_rate_marked = round(
        (days_present / days_marked * 100) if days_marked else 0, 1
    )
    # --- REMARKS ---
    remarks = None
    if current_term:
        remarks = StudentRemark.objects.filter(student=student, term=current_term, school=school,).first()

    # Fallback: if no term remark, get latest
    if not remarks:
        remarks = StudentRemark.objects.filter(student=student, school=school).order_by('-created_at').first()

    # If still no remark, create empty object for template
    if not remarks:
        remarks = {'class_teacher_remark': 'No remark yet', 'headmaster_remark': 'No remark yet'}
    grading_scales = GradingScale.objects.filter(
        school=school, term__isnull=True
    ).order_by("-min_score")

    return render(
        request,
        "accounts/transcript.html",
        {
            "student": student,
            "school": school,
            "first_year": first_year,
            "last_year": last_year,
            "setting": setting,
            "transcript_data": transcript_data,
            "grading_scales": grading_scales,
            "cumulative_avg": cumulative_percent,
            "total_subjects": results.count(),
            "date_issued": timezone.now().date(),
            "total_school_days": total_school_days,
            "days_marked": days_marked,
            "days_present": days_present,
            "days_absent": days_absent,
            "attendance_percent": attendance_percent,
            "attendance_rate_marked": attendance_rate_marked,
            "current_term": current_term,
            "remarks": remarks,
        },
    )


@login_required
def headmaster_remarks(request):
    school = request.user.school
    current_term = Term.objects.filter(school=school, is_active=True).first()

    all_classes = SchoolClass.objects.filter(school=school).order_by("name")

    selected_class_id = request.GET.get("class_id") or request.POST.get("class_id")

    students = []

    if selected_class_id:
        try:
            selected_class_id_int = int(selected_class_id)
        except:
            selected_class_id_int = selected_class_id

        student_qs = User.objects.filter(
            school=school, role="student", school_class_id=selected_class_id_int
        ).order_by("first_name")

        # ============================================
        # PAGINATION
        # ============================================
        paginator = Paginator(student_qs, 10)

        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        students = page_obj.object_list

        # ============================================
        # LOAD EXISTING REMARKS
        # ============================================
        student_ids = [s.id for s in students]

        remarks = StudentRemark.objects.filter(
            school=school, term=current_term, student_id__in=student_ids
        )

        existing = {r.student_id: r for r in remarks}

        for s in students:
            r = existing.get(s.id)

            if r and r.headmaster_remark:
                s.head_remark = r.headmaster_remark
            else:
                s.head_remark = ""

    else:
        page_obj = None

    # ============================================
    # SAVE REMARKS
    # ============================================
    if request.method == "POST":

        class_id = request.POST.get("class_id")
        page_number = request.POST.get("page", "1")

        for key, value in request.POST.items():

            if key.startswith("head_"):

                try:
                    student_id = int(key.replace("head_", ""))

                    h_remark = value.strip()

                    if h_remark:
                        StudentRemark.objects.update_or_create(
                            student_id=student_id,
                            term=current_term,
                            defaults={"school": school, "headmaster_remark": h_remark},
                        )

                except:
                    pass

        messages.success(request, "Remarks saved successfully.")

        return redirect(
            f"/headmaster/remarks/" f"?class_id={class_id}" f"&page={page_number}"
        )

    return render(
        request,
        "accounts/headmaster_remarks.html",
        {
            "all_classes": all_classes,
            "students": students,
            "selected_class_id": (str(selected_class_id) if selected_class_id else ""),
            "current_term": current_term,
            # Pagination
            "page_obj": page_obj,
            "paginator": (page_obj.paginator if page_obj else None),
        },
    )


@login_required
def school_backup(request):
    if request.user.role not in [
        "admin", "proprietor", "proprietress",
        "headmaster", "headmistress"
    ]:
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school:
        messages.error(request, "No school assigned.")
        return redirect("accounts:dashboard")

    backup_objects = []

    related_querysets = [
        FeePayment.objects.filter(fee__school=school),
        CanteenPayment.objects.filter(student_fee__school=school),
        StudentTermSummary.objects.filter(student__school=school),
        FeeAuditLog.objects.filter(transaction__school=school),
        PaymentItem.objects.filter(transaction__school=school),
        PendingPaymentItem.objects.filter(pending_payment__school=school),
        AnnouncementRead.objects.filter(announcement__school=school),
        NotificationRecipient.objects.filter(notification__school=school),
    ]

    for queryset in related_querysets:
        backup_objects.extend(
            json.loads(serializers.serialize("json", queryset))
        )

    for model in apps.get_app_config("accounts").get_models():

        if model.__name__ == "School":
            queryset = model.objects.filter(pk=school.pk)

        elif any(field.name == "school" for field in model._meta.fields):
            queryset = model.objects.filter(school=school)

        else:
            continue

        backup_objects.extend(
            json.loads(serializers.serialize("json", queryset))
        )


    backup_dir = settings.BASE_DIR / "backups" / f"school_{school.id}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"school_backup_{school.id}_"
        f"{timezone.now().strftime('%Y%m%d_%H%M%S')}.zip"
    )

    backup_file = backup_dir / filename

    with zipfile.ZipFile(backup_file, "w", zipfile.ZIP_DEFLATED) as zip_file:

        zip_file.writestr(
            "database.json",
            json.dumps(backup_objects, indent=2)
        )

        media_root = settings.MEDIA_ROOT

        for obj in backup_objects:
            fields = obj.get("fields", {})

            for value in fields.values():

                if isinstance(value, str):
                    file_path = media_root / value

                    if file_path.is_file():
                        zip_file.write(
                            file_path,
                            f"media/{value}"
                        )

    response = HttpResponse(
        backup_file.read_bytes(),
        content_type="application/zip"
    )

    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )

    return response


@login_required
def restore_school_backup(request):

    if request.method != "POST":
        return redirect("accounts:system-settings")

    if request.user.role not in [
        "admin",
        "proprietor",
        "proprietress",
        "headmaster",
        "headmistress",
    ]:
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school:
        messages.error(request, "No school assigned.")
        return redirect("accounts:system-settings")

    backup_file = request.FILES.get("backup_file")

    if not backup_file:
        messages.error(request, "Please select a backup file.")
        return redirect("accounts:system-settings")

    if not backup_file.name.lower().endswith(".zip"):
        messages.error(request, "Only ZIP backup files are allowed.")
        return redirect("accounts:system-settings")

    try:

        with zipfile.ZipFile(backup_file, "r") as zip_file:

            # -------------------------------------------------
            # Validate ZIP
            # -------------------------------------------------
            if "database.json" not in zip_file.namelist():
                messages.error(
                    request, "Invalid backup file. database.json was not found."
                )
                return redirect("accounts:system-settings")

            try:
                data = json.loads(zip_file.read("database.json").decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                messages.error(request, "Invalid backup database file.")
                return redirect("accounts:system-settings")

            # -------------------------------------------------
            # Validate School
            # -------------------------------------------------
            backup_school = next(
                (obj for obj in data if obj.get("model") == "accounts.school"), None
            )

            if not backup_school:
                messages.error(
                    request, "Invalid backup. School information was not found."
                )
                return redirect("accounts:system-settings")

            if backup_school.get("pk") != school.pk:
                messages.error(request, "This backup belongs to a different school.")
                return redirect("accounts:system-settings")

            # -------------------------------------------------
            # Keep a copy of ZIP media in memory
            # -------------------------------------------------
            media_files = {}

            for name in zip_file.namelist():

                if not name.startswith("media/"):
                    continue

                if name.endswith("/"):
                    continue

                relative_path = name[len("media/") :]

                # Security: prevent files escaping MEDIA_ROOT
                if (
                    not relative_path
                    or relative_path.startswith("/")
                    or ".." in relative_path.split("/")
                ):
                    continue

                media_files[relative_path] = zip_file.read(name)

            # -------------------------------------------------
            # Separate school record from other records
            # -------------------------------------------------
            backup_records = [
                obj for obj in data if obj.get("model") != "accounts.school"
            ]

            with transaction.atomic():

                # =================================================
                # 1. REMOVE CURRENT SCHOOL DATA
                # =================================================
                #
                # We preserve the School record itself.
                # Everything belonging to that school is removed.
                #
                # Django's cascade relationships take care of
                # records such as FeePayment, PaymentItem,
                # AnnouncementRead, etc.
                # =================================================

                school_models = []

                for model in apps.get_app_config("accounts").get_models():

                    if model.__name__ == "School":
                        continue

                    if any(field.name == "school" for field in model._meta.fields):
                        school_models.append(model)

                # Delete models with direct school relationships.
                #
                # Foreign-key cascades will remove many indirect
                # records automatically.
                for model in reversed(school_models):

                    try:
                        model.objects.filter(school=school).delete()
                    except Exception:
                        # Some models may already have been removed
                        # through a cascade.
                        continue

                # =================================================
                # 2. RESTORE SCHOOL SETTINGS / SCHOOL RECORD
                # =================================================

                for deserialized in serializers.deserialize(
                    "json", json.dumps([backup_school])
                ):
                    restored_school = deserialized.object

                    # Keep the existing School object/primary key.
                    restored_school.pk = school.pk
                    restored_school.save()

                # =================================================
                # 3. RESTORE RECORDS IN DEPENDENCY ORDER
                # =================================================

                priority = {
                    # Base records
                    "accounts.academicyear": 10,
                    "accounts.term": 20,
                    # Users/classes/subjects
                    "accounts.schoolclass": 30,
                    "accounts.subject": 40,
                    "accounts.user": 50,
                    # Academic relationships
                    "accounts.studentsubjectclass": 60,
                    "accounts.teachersubjectclass": 60,
                    # Settings
                    "accounts.systemsettings": 70,
                    "accounts.schoolsetting": 70,
                    "accounts.headmasterpermission": 70,
                    # Fees
                    "accounts.paymenttype": 80,
                    "accounts.feestructure": 90,
                    "accounts.studentfee": 100,
                    "accounts.fee": 100,
                    "accounts.dynamicfeestructure": 100,
                    "accounts.dynamicfeestructureitem": 110,
                    "accounts.dynamicstudentfeeitem": 120,
                    # Attendance / results
                    "accounts.attendancesession": 130,
                    "accounts.attendancerecord": 140,
                    "accounts.result": 140,
                    "accounts.continuousassessment": 140,
                    "accounts.studentremark": 140,
                    "accounts.studenttermsummary": 140,
                    "accounts.teacherattendance": 140,
                    # Timetable
                    "accounts.break": 150,
                    "accounts.academiccalendar": 150,
                    "accounts.timetable": 160,
                    # Payments
                    "accounts.paymenttransaction": 170,
                    "accounts.pendingpayment": 170,
                    "accounts.feepayment": 180,
                    "accounts.canteenpayment": 180,
                    "accounts.paymentitem": 180,
                    "accounts.pendingpaymentitem": 180,
                    "accounts.feeauditlog": 190,
                    # Parent relationships
                    "accounts.parentstudentlink": 200,
                    # Communication
                    "accounts.announcement": 210,
                    "accounts.announcementread": 220,
                    "accounts.notification": 230,
                    "accounts.notificationrecipient": 240,
                    "accounts.message": 250,
                    "accounts.smslog": 260,
                }

                # -------------------------------------------------
                # Sort records
                # -------------------------------------------------
                backup_records.sort(
                    key=lambda obj: priority.get(obj.get("model", "").lower(), 500)
                )

                # -------------------------------------------------
                # Restore each record
                # -------------------------------------------------
                for obj in backup_records:

                    model_name = obj.get("model", "").lower()

                    # Ignore unsupported/global records.
                    if model_name == "accounts.termsetting":
                        continue

                    try:
                        deserialized_objects = serializers.deserialize(
                            "json", json.dumps([obj])
                        )

                        for deserialized in deserialized_objects:
                            deserialized.save()

                    except Exception as restore_error:

                        raise Exception(
                            f"Could not restore "
                            f"{model_name} "
                            f"(ID {obj.get('pk')}): "
                            f"{restore_error}"
                        )

                # =================================================
                # 4. RESTORE MEDIA FILES
                # =================================================

                media_root = settings.MEDIA_ROOT
                media_root.mkdir(parents=True, exist_ok=True)

                for relative_path, file_data in media_files.items():

                    destination = media_root / relative_path

                    # Final security check
                    try:
                        destination.resolve().relative_to(media_root.resolve())
                    except ValueError:
                        continue

                    destination.parent.mkdir(parents=True, exist_ok=True)

                    destination.write_bytes(file_data)

        # =====================================================
        # SUCCESS
        # =====================================================

        messages.success(request, "School backup restored successfully.")

    except zipfile.BadZipFile:

        messages.error(request, "The selected file is not a valid ZIP backup.")

    except Exception as e:

        messages.error(request, f"Restore failed: {e}")

    return redirect("accounts:system-settings")


@login_required
def backup_database(request):
    if request.user.role not in [
        "admin",
        "proprietor",
        "proprietress",
        "headmaster",
        "headmistress",
    ]:
        messages.error(request, "You do not have permission to create a backup.")
        return redirect("accounts:dashboard")

    db_path = settings.DATABASES["default"]["NAME"]

    backup_dir = settings.BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)

    timestamp = timezone.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = backup_dir / f"school_sms_backup_{timestamp}.sqlite3"

    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(backup_path)

    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    messages.success(request, "Database backup created successfully.")

    return FileResponse(
        open(backup_path, "rb"),
        as_attachment=True,
        filename=backup_path.name,
    )


@login_required
def basic_daily_fee_settings(request):
    if request.user.role not in ["headmaster", "headmistress"]:
        messages.error(request, "You do not have permission to access this page.")
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school or school.edition != "basic":
        messages.error(request, "This feature is only available for Basic schools.")
        return redirect("accounts:dashboard")

    classes = SchoolClass.objects.filter(school=school).order_by("stage", "name")

    if request.method == "POST":
        class_id = request.POST.get("school_class")
        amount = request.POST.get("amount")

        school_class = get_object_or_404(SchoolClass, id=class_id, school=school)

        BasicDailyFeeSetting.objects.update_or_create(
            school=school, school_class=school_class, defaults={"amount": amount}
        )

        messages.success(
            request, f"Daily fee for {school_class.name} updated successfully."
        )

        return redirect("accounts:basic_daily_fee_settings")

    settings = BasicDailyFeeSetting.objects.filter(school=school).select_related(
        "school_class"
    )

    return render(
        request,
        "accounts/basic_daily_fee_settings.html",
        {
            "classes": classes,
            "settings": settings,
            "is_basic_school": True,
        },
    )


@login_required
def basic_daily_fee_collection(request):
    if request.user.role != "teacher":
        messages.error(request, "Only teachers can record daily fees.")
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school or school.edition != "basic":
        messages.error(request, "This feature is only available for Basic schools.")
        return redirect("accounts:dashboard")

    # --------------------------------------------------
    # CLASS TEACHER
    # --------------------------------------------------

    school_class = request.user.class_teacher_of

    if not school_class or school_class.school != school:
        messages.error(request, "You are not assigned as a class teacher.")
        return redirect("accounts:dashboard")

    # --------------------------------------------------
    # STUDENTS
    # --------------------------------------------------

    students = User.objects.filter(
        school=school, role="student", school_class=school_class
    ).order_by("first_name", "last_name")

    # --------------------------------------------------
    # DAILY FEE
    # --------------------------------------------------

    daily_fee = BasicDailyFeeSetting.objects.filter(
        school=school, school_class=school_class
    ).first()

    if not daily_fee:
        messages.error(request, "A daily fee has not been configured for your class.")
        return redirect("accounts:dashboard")

    # --------------------------------------------------
    # ACADEMIC YEARS
    # --------------------------------------------------

    academic_years = AcademicYear.objects.filter(school=school).order_by("-name")

    selected_academic_year_id = request.GET.get("academic_year")

    selected_academic_year = None

    if selected_academic_year_id:
        selected_academic_year = academic_years.filter(
            id=selected_academic_year_id
        ).first()

    # --------------------------------------------------
    # TERMS
    # --------------------------------------------------

    terms = (
        Term.objects.filter(school=school)
        .select_related("academic_year")
        .order_by("term_number")
    )

    if selected_academic_year:
        terms = terms.filter(academic_year=selected_academic_year)

    selected_term_id = request.GET.get("term")

    selected_term = None

    if selected_term_id:
        selected_term = terms.filter(id=selected_term_id).first()

    # --------------------------------------------------
    # DATE
    # --------------------------------------------------

    selected_date_string = request.GET.get("date")

    if selected_date_string:
        try:
            selected_date = datetime.strptime(selected_date_string, "%Y-%m-%d").date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    # --------------------------------------------------
    # VIEW MODE
    # --------------------------------------------------

    view_mode = request.GET.get("view", "today")

    if view_mode not in ["today", "week", "month", "all"]:
        view_mode = "today"

    # --------------------------------------------------
    # WEEKEND
    # --------------------------------------------------

    is_weekend = selected_date.weekday() >= 5

    # --------------------------------------------------
    # PAYMENTS FOR SELECTED DATE
    # --------------------------------------------------

    selected_date_collections = BasicDailyFeeCollection.objects.filter(
        school=school, school_class=school_class, date=selected_date
    ).select_related("student", "academic_year", "term")

    paid_student_ids = set(
        selected_date_collections.values_list("student_id", flat=True)
    )

    # --------------------------------------------------
    # COLLECTION HISTORY
    # --------------------------------------------------

    collections = BasicDailyFeeCollection.objects.filter(
        school=school, school_class=school_class
    ).select_related("student", "academic_year", "term")

    if selected_academic_year:
        collections = collections.filter(academic_year=selected_academic_year)

    if selected_term:
        collections = collections.filter(term=selected_term)

    if view_mode == "today":

        collections = collections.filter(date=selected_date)

    elif view_mode == "week":

        week_start = selected_date - timedelta(days=selected_date.weekday())

        week_end = week_start + timedelta(days=6)

        collections = collections.filter(date__range=[week_start, week_end])

    elif view_mode == "month":

        month_start = selected_date.replace(day=1)

        if selected_date.month == 12:
            month_end = selected_date.replace(
                year=selected_date.year + 1, month=1, day=1
            ) - timedelta(days=1)
        else:
            month_end = selected_date.replace(
                month=selected_date.month + 1, day=1
            ) - timedelta(days=1)

        collections = collections.filter(date__range=[month_start, month_end])

    collections = collections.order_by("-date", "-created_at")

    collection_total = collections.aggregate(total=Sum("amount"))["total"] or 0
        # --------------------------------------------------
    # PAGINATION
    # --------------------------------------------------

    student_search = request.GET.get("student_search", "").strip()

    if student_search:
        search_parts = student_search.split()

        if len(search_parts) >= 2:
            students = students.filter(
                Q(first_name__icontains=search_parts[0]) &
                Q(last_name__icontains=search_parts[-1])
            )
        else:
            students = students.filter(
                Q(first_name__icontains=student_search) |
                Q(last_name__icontains=student_search)
            )

    students_paginator = Paginator(students, 10)

    students_page_number = request.GET.get("students_page", 1)

    students_page = students_paginator.get_page(students_page_number)


    collections_paginator = Paginator(collections, 10)

    collections_page_number = request.GET.get("history_page", 1)

    collections_page = collections_paginator.get_page(
        collections_page_number
    )

    # --------------------------------------------------
    # RECORD PAYMENT
    # --------------------------------------------------

    if request.method == "POST":

        student_id = request.POST.get("student")

        payment_date_string = request.POST.get("date")

        try:
            payment_date = datetime.strptime(payment_date_string, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            messages.error(request, "Please select a valid payment date.")
            return redirect("accounts:basic_daily_fee_collection")

        if payment_date.weekday() >= 5:
            messages.error(request, "Daily fees cannot be collected on weekends.")
            return redirect(f"{request.path}?date={payment_date}")

        if payment_date > timezone.localdate():
            messages.error(request, "You cannot record a payment for a future date.")
            return redirect(f"{request.path}?date={payment_date}")

        student = get_object_or_404(
            User,
            id=student_id,
            school=school,
            role="student",
            school_class=school_class,
        )

        # Find the term covering the payment date
        payment_term = (
            Term.objects.filter(
                school=school, start_date__lte=payment_date, end_date__gte=payment_date
            )
            .select_related("academic_year")
            .first()
        )

        if not payment_term:
            messages.error(
                request, "No academic term covers the selected payment date."
            )
            return redirect(f"{request.path}?date={payment_date}")

        # Prevent duplicate payment
        already_paid = BasicDailyFeeCollection.objects.filter(
            school=school, student=student, school_class=school_class, date=payment_date
        ).exists()

        if already_paid:
            messages.warning(
                request,
                f"{student.get_full_name()} has already paid "
                f"the daily fee for {payment_date}.",
            )
            return redirect(f"{request.path}?date={payment_date}")

        BasicDailyFeeCollection.objects.create(
            school=school,
            academic_year=payment_term.academic_year,
            term=payment_term,
            student=student,
            school_class=school_class,
            amount=daily_fee.amount,
            date=payment_date,
            collected_by=request.user,
        )

        messages.success(request, f"Payment recorded for {student.get_full_name()}.")

        return redirect(f"{request.path}?date={payment_date}")

    # --------------------------------------------------
    # PAGE
    # --------------------------------------------------

    return render(
        request,
        "accounts/basic_daily_fee_collection.html",
        {
            "school_class": school_class,
            "students": students_page,
            "daily_fee": daily_fee,
            "academic_years": academic_years,
            "selected_academic_year": selected_academic_year,
            "terms": terms,
            "selected_term": selected_term,
            "selected_date": selected_date,
            "view_mode": view_mode,
            "is_weekend": is_weekend,
            "selected_date_collections": selected_date_collections,
            "paid_student_ids": paid_student_ids,
            "collections": collections_page,
            "collection_total": collection_total,
            "students_page": students_page,
            "collections_page": collections_page,
            "student_search": student_search,
            "is_basic_school": True,
        },
    )


@login_required
def basic_daily_fee_monitoring(request):
    if request.user.role not in ["headmaster", "headmistress"]:
        messages.error(
            request,
            "You do not have permission to access this page."
        )
        return redirect("accounts:dashboard")

    school = request.user.school

    if not school or school.edition != "basic":
        messages.error(
            request,
            "This feature is only available for Basic schools."
        )
        return redirect("accounts:dashboard")

    # -----------------------------
    # SELECTED DATE
    # -----------------------------
    date_string = request.GET.get("date")

    if date_string:
        try:
            selected_date = datetime.strptime(
                date_string,
                "%Y-%m-%d"
            ).date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    # -----------------------------
    # SKIP WEEKENDS
    # -----------------------------
    if selected_date.weekday() >= 5:
        selected_date = selected_date - timedelta(
            days=selected_date.weekday() - 4
        )

    # -----------------------------
    # PREVIOUS SCHOOL DAY
    # -----------------------------
    previous_date = selected_date - timedelta(days=1)

    while previous_date.weekday() >= 5:
        previous_date -= timedelta(days=1)

    # -----------------------------
    # NEXT SCHOOL DAY
    # -----------------------------
    next_date = selected_date + timedelta(days=1)

    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)

    # -----------------------------
    # COLLECTIONS FOR SELECTED DATE
    # -----------------------------
    collections = BasicDailyFeeCollection.objects.filter(
        school=school,
        date=selected_date
    ).select_related(
        "student",
        "school_class",
        "collected_by"
    ).order_by(
        "school_class__stage",
        "school_class__name",
        "student__first_name",
        "student__last_name"
    )

    # -----------------------------
    # SCHOOL CLASSES
    # -----------------------------
    classes = SchoolClass.objects.filter(
        school=school
    ).order_by(
        "stage",
        "name"
    )

    # -----------------------------
    # OVERALL TOTAL
    # -----------------------------
    total_collected = collections.aggregate(
        total=Sum("amount")
    )["total"] or 0

    # -----------------------------
    # CLASS TOTALS
    # -----------------------------
    class_totals = []

    for school_class in classes:

        class_students = User.objects.filter(
            school=school,
            role="student",
            school_class=school_class
        )

        class_collections = collections.filter(
            school_class=school_class
        )

        paid_student_ids = set(
            class_collections.values_list(
                "student_id",
                flat=True
            )
        )

        total_students = class_students.count()
        paid_count = len(paid_student_ids)
        unpaid_count = max(
            total_students - paid_count,
            0
        )

        class_total = class_collections.aggregate(
            total=Sum("amount")
        )["total"] or 0
        unpaid_students = class_students.exclude(id__in=paid_student_ids).order_by("first_name", "last_name")

        class_totals.append({
            "school_class": school_class,
            "total_students": total_students,
            "paid_count": paid_count,
            "unpaid_count": unpaid_count,
            "total": class_total,
            "unpaid_students": unpaid_students,
            "collections": class_collections,
        })

    return render(
        request,
        "accounts/basic_daily_fee_monitoring.html",
        {
            "school": school,
            "collections": collections,
            "classes": classes,
            "class_totals": class_totals,
            "total_collected": total_collected,
            "selected_date": selected_date,
            "previous_date": previous_date,
            "next_date": next_date,
            "is_basic_school": True,
        },
    )


@login_required
def grading_settings(request):
    if (
        request.user.role
        not in [
            "admin",
            "proprietor",
            "proprietress",
            "headmaster",
            "headmistress",
        ]
        and not request.user.is_superuser
    ):
        messages.error(request, "Permission denied")
        return redirect("accounts:dashboard")

    school = request.user.school

    # EDIT
    edit_id = request.GET.get("edit")
    editing_grading = None

    if edit_id:
        editing_grading = get_object_or_404(GradingScale, id=edit_id, school=school)

    if request.method == "POST":

        # DELETE
        delete_id = request.POST.get("delete_id")

        if delete_id:
            grading = get_object_or_404(
                GradingScale,
                id=delete_id,
                school=school
            )

            grading.delete()

            messages.success(
                request,
                "Grading scale deleted successfully."
            )

            return redirect("accounts:grading-settings")

        grading_id = request.POST.get("grading_id")

        if grading_id:
            # UPDATE EXISTING GRADING SCALE
            grading = get_object_or_404(GradingScale, id=grading_id, school=school)

            form = GradingScaleForm(request.POST, instance=grading, school=school)
        else:
            # CREATE NEW GRADING SCALE
            form = GradingScaleForm(request.POST, school=school)

        if form.is_valid():
            grading = form.save(commit=False)
            grading.school = school
            grading.save()

            messages.success(request, "Grading scale saved successfully.")

            return redirect("accounts:grading-settings")

    else:
        if editing_grading:
            form = GradingScaleForm(instance=editing_grading, school=school)
        else:
            form = GradingScaleForm(school=school)

    grading_scales = GradingScale.objects.filter(school=school).select_related("term")

    return render(
        request,
        "accounts/grading_settings.html",
        {
            "form": form,
            "grading_scales": grading_scales,
            "editing_grading": editing_grading,
        },
    )
