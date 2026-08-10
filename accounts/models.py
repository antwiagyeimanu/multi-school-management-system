from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.files.base import ContentFile
from datetime import date
from PIL import Image
from io import BytesIO
import random
from decimal import Decimal
from django.db.models import Sum
from django.core.validators import MinValueValidator
import string
from django.contrib.auth import get_user_model
# Add these imports at top if not there


STAGE_GROUP_MAP = {
    'creche': ['Creche'],
    'nursery': ['Nursery'],
    'kg': ['KG 1', 'KG 2', 'Kindergarten'],
    'lower_primary': ['Primary 1', 'Primary 2', 'Primary 3'],
    'upper_primary': ['Primary 4', 'Primary 5', 'Primary 6'],
    'jhs': ['JHS 1', 'JHS 2', 'JHS 3'],
    'shs': ['SHS 1', 'SHS 2', 'SHS 3'],
}

STAGE_CHOICES = [
    ('creche', 'Creche'),
    ('nursery', 'Nursery'),
    ('kg', 'KG'),
    ('lower_primary', 'Lower Primary'),
    ('upper_primary', 'Upper Primary'),
    ('jhs', 'JHS'),
    ('shs', 'SHS'),
]

TERM_CHOICES = [
    ('term1', 'First Term'),
    ('term2', 'Second Term'),
    ('term3', 'Third Term'),
]

# -------------------------------
# School model
# -------------------------------
class School(models.Model):
    name = models.CharField(max_length=200, unique=True)
    allows_student_login = models.BooleanField(default=False)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    logo = models.ImageField(upload_to='school_logos/', blank=True, null=True)
    email = models.EmailField(blank=True, null=True, verbose_name="School Email")
    gps_address = models.CharField(max_length=50, blank=True, null=True, verbose_name="GPS/Digital Address")
    can_teachers_manage_students = models.BooleanField(default=False)

    SCHOOL_TYPE = [
        ('basic', 'Basic (Nursery - JHS)'),
        ('shs', 'Senior High School'),
    ]
    level = models.CharField(
        max_length=10,
        choices=SCHOOL_TYPE,
        default='basic'
    )

    # ✅ PAYMENT SETTINGS - UPDATED
    PAYMENT_MODE_CHOICES = [
        ('disabled', 'Disabled - No online payments'),
        ('cash', 'Cash Only - Pay at school'),
        ('manual', 'Manual - Show MoMo/Bank Details'),
        ('automatic', 'Automatic - Online Gateway'),
    ]
    payment_mode = models.CharField(
        max_length=10, 
        choices=PAYMENT_MODE_CHOICES, 
        default='disabled',
        help_text="OLD - Keep for now"
    )
    
    # 👇 NEW FIELDS - THESE 3 LINES ARE NEW
    accept_cash = models.BooleanField(default=False, verbose_name="Cash at School")
    accept_manual = models.BooleanField(default=False, verbose_name="Manual Transfer")
    accept_automatic = models.BooleanField(default=False, verbose_name="Online Gateway")
    accept_momo = models.BooleanField(default=False)
    accept_bank = models.BooleanField(default=False)
    # === SMS SETTINGS - ADD THIS ===
    sms_enabled = models.BooleanField(default=False, verbose_name="Enable SMS Notifications")
    sms_test_mode = models.BooleanField(default=True, verbose_name="SMS Test Mode - Don't send real SMS")
    
    SMS_PROVIDER_CHOICES = [
        ('hubtel', 'Hubtel (Recommended for Ghana)'),
        ('mnotify', 'MNotify'),
    ]
    sms_provider = models.CharField(
        max_length=20,
        choices=SMS_PROVIDER_CHOICES,
        default='hubtel',
        blank=True,
        null=True
    )
    # Reuse hubtel_client_id and hubtel_client_secret you already have!
    # Just add sender ID for SMS
    sms_sender_id = models.CharField(max_length=11, blank=True, null=True, verbose_name="SMS Sender ID e.g SCHOOL", help_text="Max 11 chars, e.g. ADISSCH")
    
    # For MNotify if they use it
    mnotify_api_key = models.CharField(max_length=200, blank=True, null=True)
    mnotify_sender_id = models.CharField(max_length=11, blank=True, null=True)
    
    # Gateway Selection
    PAYMENT_GATEWAY_CHOICES = [
        ('hubtel', 'Hubtel'),
        ('paystack', 'Paystack'),
        ('flutterwave', 'Flutterwave'),
    ]
    payment_gateway = models.CharField(
        max_length=20,
        choices=PAYMENT_GATEWAY_CHOICES,
        default='hubtel',
        blank=True,
        null=True,
        help_text="Select gateway if Online Gateway is enabled"
    )
    
    # For Manual Payment
    momo_number = models.CharField(max_length=15, blank=True, null=True, verbose_name="School MoMo Number")
    momo_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="School MoMo Name")
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_number = models.CharField(max_length=20, blank=True, null=True)
    account_name = models.CharField(max_length=200, blank=True, null=True)
    
    # For Automatic Payment - Paystack
    paystack_public_key = models.CharField(max_length=200, blank=True, null=True)
    paystack_secret_key = models.CharField(max_length=200, blank=True, null=True)
    
    # For Automatic Payment - Hubtel
    hubtel_client_id = models.CharField(max_length=200, blank=True, null=True)
    hubtel_client_secret = models.CharField(max_length=200, blank=True, null=True)
    hubtel_merchant_account = models.CharField(max_length=100, blank=True, null=True, verbose_name="Hubtel Merchant Account Number")
    
    # For Automatic Payment - Flutterwave
    flutterwave_public_key = models.CharField(max_length=200, blank=True, null=True)
    flutterwave_secret_key = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return self.name

# -------------------------------
# User model
# -------------------------------
class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin/Proprietor'),  
        ('board_director', 'Board of Directors'),    
        ('headmaster', 'Headmaster'),       
        ('accountant', 'Accountant'),       
        ('bursar', 'Bursar'),               
        ('hod', 'Head of Department'),      
        ('teacher', 'Teacher'),             
        ('student', 'Student'),             
        ('parent', 'Parent'),
        ('support_staff', 'Support Staff - Kitchen/Garden/Security/Canteen/Attendant/Driver/Cleaner'),             
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
        # 🔐 NEW FIELD (THIS IS IMPORTANT)
    can_login = models.BooleanField(default=True)

    GENDER_CHOICES = [
    ('M', 'Male'),
    ('F', 'Female'),
    ('NB', 'Non-binary'),
    ('T', 'Transgender'),
    ('O', 'Other'),
    ('N', 'Prefer not to say'),
]

    gender = models.CharField(max_length=2, choices=GENDER_CHOICES, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    admission_date = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=100, null=True, blank=True)
    parent_guardian_name = models.CharField(max_length=200, null=True, blank=True)
    parent_guardian_phone = models.CharField(max_length=20, null=True, blank=True)

    TITLE_CHOICES = [
        ('Mr', 'Mr'),
        ('Mrs', 'Mrs'),
        ('Miss', 'Miss'),
    ]
    title = models.CharField(max_length=10,
                             choices=TITLE_CHOICES,
                             blank=True,
                             null=True
    )
    phone = models.CharField(max_length=15, blank=True, null=True)
    middle_name = models.CharField(max_length=50, blank=True, null=True)
    student_number = models.CharField(max_length=20, blank=True, null=True, unique=True)
    is_class_teacher = models.BooleanField(default=False)
    class_teacher_of = models.ForeignKey('SchoolClass', null=True, blank=True, on_delete=models.SET_NULL, related_name='class_teacher')
    staff_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    school = models.ForeignKey('School', on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    is_password_changed = models.BooleanField(default=False)
    school_class = models.ForeignKey('SchoolClass', on_delete=models.SET_NULL, null=True, blank=True, related_name='students')
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, limit_choices_to={'role': 'parent'}, related_name='children')
    photo = models.ImageField(upload_to='student_photos/', null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    qualification = models.CharField(max_length=200, blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    national_id_card = models.CharField(max_length=50, unique=True, null=True, blank=True, verbose_name="National ID Card Number")
    emergency_contact_name = models.CharField(max_length=150, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    emergency_contact_relationship = models.CharField(max_length=50, blank=True, null=True)
    # Parent extra fields
    occupation = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    relationship_type = models.CharField(max_length=30, blank=True, null=True, help_text="Father/Mother/Guardian")
    @property
    def is_real_class_teacher(self):
        return (
            self.role == "teacher"
            and self.is_class_teacher
            and self.class_teacher_of is not None
        )
    @property
    def display_role(self):
        if self.is_real_class_teacher:
            return "Class Teacher"
        return "Teacher"
    
    
    

    

    RELIGION_CHOICES = [
    ('CHRISTIAN', 'Christianity'),
    ('MUSLIM', 'Islam'),
    ('TRADITIONAL', 'Traditional'),
    ('HINDU', 'Hinduism'),
    ('BUDDHIST', 'Buddhism'),
    ('ATHEIST', 'Atheist'),
    ('OTHER', 'Other'),
    ('NONE', 'None/Prefer not to say'),
]
    religion = models.CharField(
        max_length=20,
        choices=RELIGION_CHOICES,
        null=True,
        blank=True
    )



    def save(self, *args, **kwargs):
        if self.school:
            school_initials = ''.join([word[0] for word in self.school.name.split()]).upper()
            
            if self.role in ['teacher','accountant','bursar','hod','headmaster','admin','board_director'] and not self.staff_id:
                role_codes = {
                    'teacher': 'TCH',
                    'accountant': 'ACC',
                    'bursar': 'BUR',
                    'hod': 'HOD',
                    'headmaster': 'HMT',
                    'admin': 'ADM',
                    'board_director': 'BOD',
                }
                role_code = role_codes.get(self.role, 'STF')

                while True:
                    characters = string.ascii_uppercase + string.digits

                    random_part = ''.join(
                        random.choice(characters) for _ in range(4)
                    )

                    # Ensure at least one digit exists
                    if any(char.isdigit() for char in random_part):

                        staff_id = f"{school_initials}/{role_code}/{random_part}"

                        if not User.objects.filter(staff_id=staff_id).exists():
                            self.staff_id = staff_id
                            break


                # Student Number
                if self.role == 'student' and not self.student_number:

                    admission_year = str(date.today().year)[-2:]  # 2026 -> 26

                    while True:

                        characters = string.ascii_uppercase + string.digits

                        random_part = ''.join(
                            random.choice(characters) for _ in range(2)
                        )

                        student_number = (
                            f"{school_initials}/STU/{admission_year}/{random_part}"
                        )

                        if not User.objects.filter(student_number=student_number).exists():
                            self.student_number = student_number
                            break

        # Your photo resize code - KEEP THIS AS IS
        if self.photo:
            img = Image.open(self.photo)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            if img.height > 500 or img.width > 500:
                img.thumbnail((500, 500))
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            file_name = f"{self.username or self.id or 'user'}_photo.jpg"
            self.photo.save(file_name, ContentFile(buffer.getvalue()), save=False)

        super().save(*args, **kwargs)
    def __str__(self):
        if self.role == "student":
            if self.student_number:
                return f"[{self.student_number}] {self.get_full_name()}"
            return self.get_full_name()

        if self.role == "teacher":
            return f"{self.get_full_name()} ({self.staff_id or 'No ID'})"

        if self.staff_id:
            return f"{self.get_full_name()} ({self.staff_id})"

        return self.username


# -------------------------------
# Subjects
# -------------------------------
class Subject(models.Model):
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=100)
    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True)
    teachers = models.ManyToManyField(User, blank=True, related_name='teaching_subjects')

    def __str__(self):
        return f"{self.code} - {self.name}"

# -------------------------------
# School Class
# -------------------------------
class SchoolClass(models.Model):
    name = models.CharField(max_length=50)
    allows_student_login = models.BooleanField(default=False)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES) 
    stage_code = models.CharField(max_length=50, null=True, blank=True)
    school = models.ForeignKey('School', on_delete=models.CASCADE)
    subjects = models.ManyToManyField('Subject')
    
    def __str__(self):
        return self.name
    def save(self, *args, **kwargs):
        if not self.stage_code:
            mapping = {
                'lower_primary': 'LP',
                'upper_primary': 'UP',
                'jhs': 'JHS',
                'shs': 'SHS',
                'creche': 'CR',
                'nursery': 'NU',
                'kg': 'KG',
            }
            self.stage_code = mapping.get(self.stage, self.stage)

        super().save(*args, **kwargs)

# -------------------------------
# TeacherSubjectClass - ONLY ONE COPY
# -------------------------------
class TeacherSubjectClass(models.Model):
    teacher = models.ForeignKey('User', on_delete=models.CASCADE, limit_choices_to={'role': 'teacher'})
    subject = models.ForeignKey('Subject', on_delete=models.CASCADE, null=True, blank=True)
    school_class = models.ForeignKey('accounts.SchoolClass', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        unique_together = ('teacher', 'subject', 'school_class')

    def __str__(self):
        school_class_str = str(self.school_class) if self.school_class else "No Class"
        subject_str = self.subject.name if self.subject else "No Subject"
        return f"{school_class_str} - {subject_str}"

# -------------------------------
# System settings
# -------------------------------
class SystemSettings(models.Model):
    site_name = models.CharField(max_length=200)
    max_students_per_class = models.IntegerField(default=30)

    def __str__(self):
        return self.site_name

# -------------------------------
# Messages
# -------------------------------
class Message(models.Model):
    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True) # ADD THIS ONE LINE HERE
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_messages')
    subject = models.CharField(max_length=200)
    body = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs): # ADD THIS WHOLE SAVE FUNCTION
        if not self.school and self.sender_id:
            self.school = self.sender.school
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sender} → {self.receiver} | {self.subject}"
# -------------------------------
# Attendance
# -------------------------------
class AttendanceSession(models.Model):
    """
    This becomes 1 row in your admin table.
    Example: Primary 5 + English + Apr 17 + Teacher = 1 Session
    """
    STATUS_CHOICES = [
        ('P', 'Present'),
        ('A', 'Absent'),
        ('L', 'Late'),
        ('E', 'Excused'), 
    ]

    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True) 
    school_class = models.ForeignKey('SchoolClass', on_delete=models.CASCADE)
    subject = models.ForeignKey('Subject', on_delete=models.CASCADE)
    teacher = models.ForeignKey('User', on_delete=models.CASCADE,
                               limit_choices_to={'role': 'teacher'},
                               related_name='taught_sessions')
    date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('school_class', 'subject', 'date')
        ordering = ['-date', 'school_class__name', 'subject__name']
        verbose_name = "Attendance Session"
        verbose_name_plural = "Attendance Sessions"

    def save(self, *args, **kwargs):
        if not self.school and self.school_class_id:
            self.school = self.school_class.school
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.school_class.name} | {self.subject.name} | {self.date}"

    @property
    def total_students(self):
        return self.records.count()

    @property
    def present_count(self):
        return self.records.filter(status='P').count()

    @property
    def absent_count(self):
        return self.records.filter(status='A').count()

    @property
    def late_count(self):
        return self.records.filter(status='L').count()

class AttendanceRecord(models.Model):
    """
    These are the individual students. They only show when admin clicks "View"
    """
    STATUS_CHOICES = [
    ('', '---------'),  
    ('P', 'Present'),
    ('A', 'Absent'),
    ('L', 'Late'),
    ('E', 'Excused'),
]

    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True) 
    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name='records')
    student = models.ForeignKey('User', on_delete=models.CASCADE,
                               related_name='attendance_records',
                               limit_choices_to={'role': 'student'})
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='')  
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('session', 'student') 
        ordering = ['student__first_name']
        verbose_name = "Attendance Record"
        verbose_name_plural = "Attendance Records"
    def save(self, *args, **kwargs): 
        if not self.school and self.school_class_id:
            self.school = self.school_class.school
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.get_full_name()} - {self.get_status_display()}"


# -------------------------------
# Results
# -------------------------------
class Result(models.Model):
    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True) 
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='results')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    exam_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, null=True, blank=True)

    date = models.DateField(auto_now_add=True)
    TERM_CHOICES = [(1, 'First Term'), (2, 'Second Term'), (3, 'Third Term')]
    term = models.ForeignKey('Term', on_delete=models.CASCADE)
    academic_year = models.ForeignKey(
    'AcademicYear',
    on_delete=models.SET_NULL,
    null=True,
    blank=True
)
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted to Admin'),
        ('published', 'Published to Parents'),
        ('returned', 'Returned for Correction'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    returned_reason = models.TextField(blank=True, null=True)

    

    @property
    def class_score(self):
        from accounts.models import ContinuousAssessment

        return ContinuousAssessment.objects.filter(
            student=self.student,
            subject=self.subject,
            term=self.term,
            academic_year=self.academic_year
        ).aggregate(total=Sum('score'))['total'] or 0
    @property
    def grade(self):
        pct = self.percentage

        if pct >= 80:
            return "A"
        elif pct >= 70:
            return "B"
        elif pct >= 60:
            return "C"
        elif pct >= 50:
            return "D"
        elif pct >= 40:
            return "E"
        else:
            return "F"


    @property
    def remark(self):
        pct = self.percentage

        if pct >= 80:
            return "Excellent"
        elif pct >= 70:
            return "Very Good"
        elif pct >= 60:
            return "Good"
        elif pct >= 50:
            return "Pass"
        elif pct >= 40:
            return "Weak"
        else:
            return "Fail"

    @property
    def total_score(self):
            return float(self.class_score or 0) + float(self.exam_score or 0)
    @property
    def percentage(self):
        total_max = 100

        if total_max == 0:
            return 0

        return round((float(self.total_score) / float(total_max)) * 100, 1)


    def clean(self):
        if self.exam_score and self.exam_score > 100:
            raise ValidationError({
                'exam_score': 'Exam score cannot be more than 100'
            })

    def save(self, *args, **kwargs):
        if not self.school and self.student_id:
            self.school = self.student.school 
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        unique_together = ('student', 'subject', 'term', 'academic_year')

# -------------------------------
# Fees
# -------------------------------
class Fee(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    description = models.CharField(max_length=200, default="School Fees")
    amount_due = models.DecimalField(max_digits=8, decimal_places=2)  # What student OWES
    amount_paid = models.DecimalField(max_digits=8, decimal_places=2, default=0)  # What student PAID
    due_date = models.DateField(null=True, blank=True)
    date_paid = models.DateField(null=True, blank=True)  # Only set when paid
    year = models.IntegerField() 
    academic_year = models.ForeignKey(
        'AcademicYear',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    status = models.CharField(
        max_length=10, 
        choices=[('unpaid', 'Unpaid'), ('paid', 'Paid')], 
        default='unpaid'
    )

    @property
    def balance(self):
        return self.amount_due - self.amount_paid

    def __str__(self):
        return f"{self.student.username} - {self.description}"


# -------------------------------
# StudentSubjectClass
# -------------------------------
class StudentSubjectClass(models.Model):
    student = models.ForeignKey(
        "User", on_delete=models.CASCADE, limit_choices_to={"role": "student"}
    )
    subject = models.ForeignKey("Subject", on_delete=models.CASCADE)
    school_class = models.ForeignKey(
        "SchoolClass", on_delete=models.CASCADE, null=True, blank=True
    )
    school = models.ForeignKey(
        "School", on_delete=models.CASCADE, null=True, blank=True
    )  

    class Meta:
        unique_together = (
            "student",
            "subject",
            "school_class",
            "school",
        )  

    def __str__(self):
        return f"{self.student.username} - {self.subject.name} ({self.school_class.name if self.school_class else 'None'})"


# -------------------------------
# Student Term Summary
# -------------------------------
class StudentTermSummary(models.Model):
    student = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'student'},
        related_name='term_summaries'
    )
    term = models.CharField(max_length=20, help_text="Example: Term 1")
    academic_year = models.ForeignKey(
    'AcademicYear',
    on_delete=models.SET_NULL,
    null=True,
    blank=True
)
    school_class = models.ForeignKey('SchoolClass', on_delete=models.CASCADE)
    rank = models.IntegerField(null=True, blank=True)
    term_total = models.FloatField(default=0)
    term_average = models.FloatField(default=0)
    term_grade = models.CharField(max_length=2, blank=True)
    term_remark = models.CharField(max_length=50, blank=True)
    date_created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'term', 'academic_year')
        verbose_name = "Student Term Summary"
        verbose_name_plural = "Student Term Summaries"
        ordering = ['school_class__name', 'rank', 'student__last_name']

    def __str__(self):
        return f"{self.student.first_name} {self.student.last_name} - {self.term} {self.academic_year} - Rank {self.rank}"

# -------------------------------
# Term - Every school sets their own dates
# -------------------------------
class Term(models.Model):
    TERM_CHOICES = [
        (1, 'First Term'),
        (2, 'Second Term'),
        (3, 'Third Term'),
    ]
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='terms')
    term_number = models.IntegerField(choices=TERM_CHOICES)
    academic_year = models.ForeignKey('AcademicYear', on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    days_opened = models.IntegerField(help_text="Total school days this term")
    next_term_begins = models.DateField(null=True, blank=True)
    ca_total = models.IntegerField(help_text="Total CA marks for this term")
    exam_total = models.IntegerField(help_text="Total Exam marks for this term")
    is_active = models.BooleanField(default=False)
    def save(self, *args, **kwargs):
        if self.is_active:
            Term.objects.filter(
                school=self.school,
                is_active=True
            ).exclude(
                pk=self.pk
            ).update(is_active=False)

        super().save(*args, **kwargs)
    class Meta:
        unique_together = ('school', 'term_number', 'academic_year')
        ordering = ['school', 'academic_year', 'term_number']

        

    def __str__(self):
        return f" {self.get_term_number_display()}"

def get_current_academic_year():
    today = date.today()
    try:
        setting = SchoolSetting.objects.first()
        start_month = setting.academic_year_start_month if setting else 9
    except:
        start_month = 9
    year = today.year
    if today.month >= start_month:  
        return f"{year}/{year + 1}"
    else:
        return f"{year - 1}/{year}"


class AcademicYear(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='academic_years', null=True, blank=True)
    name = models.CharField(max_length=20, unique=True)  
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    def save(self, *args, **kwargs):
        if self.is_active:
            AcademicYear.objects.filter(
                school=self.school,
                is_active=True
            ).exclude(
                pk=self.pk
            ).update(is_active=False)

        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-name']

    def __str__(self):
        return self.name


# NO SPACES BEFORE 'class' - start at column 1
class StudentFee(models.Model):
    student = models.ForeignKey(
        'accounts.User', 
        on_delete=models.CASCADE, 
        related_name='fees',
        limit_choices_to={'role': 'student'}
    )
    school = models.ForeignKey(
    School,
    on_delete=models.CASCADE,
    related_name='student_fees'
)
    term = models.ForeignKey('Term', on_delete=models.CASCADE)
    academic_year = models.CharField(max_length=20)
    stage = models.CharField(max_length=50)
    
    school_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pta_dues = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    computer_levy = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    exam_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    canteen_type = models.CharField(max_length=10, choices=[('terminal', 'Per Term')], default='terminal')
    canteen_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_school_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_pta_dues = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_computer_levy = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_exam_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_canteen = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_other_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    development_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    boarding_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hostel_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_boarding_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_hostel_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid_development_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    
    class Meta:
        unique_together = ('student', 'term', 'academic_year')  # add academic_year back
        
    def amount_paid(self):
        return (
            self.amount_paid_school_fees +
            self.amount_paid_pta_dues +
            self.amount_paid_computer_levy +
            self.amount_paid_exam_fees +
            self.amount_paid_canteen +
            self.amount_paid_other_fees +
            self.amount_paid_boarding_fee +
            self.amount_paid_hostel_fee +
            self.amount_paid_development_fee 
        )

    def balance(self):
        return self.total_amount - self.amount_paid()

    def __str__(self):
        return f"{self.student.first_name} - {self.term} {self.academic_year}"

    def save(self, *args, **kwargs):
        self.total_amount = (
            self.school_fees + 
            self.pta_dues + 
            self.boarding_fee +
            self.hostel_fee +
            self.development_fee +
            self.computer_levy + 
            self.exam_fees + 
            self.canteen_amount +
            self.other_fees
        )
        super().save(*args, **kwargs)


class FeeStructure(models.Model):
    STAGE_CHOICES = [
        ('creche', 'Creche'),
        ('nursery', 'Nursery'),
        ('kg', 'KG'),
        ('lower_primary', 'Lower Primary'),
        ('upper_primary', 'Upper Primary'),
        ('jhs', 'JHS'),
        ('shs', 'SHS'),
    ]

    school = models.ForeignKey(School, on_delete=models.CASCADE)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES)  
    term = models.ForeignKey('Term', on_delete=models.CASCADE)
    academic_year = models.CharField(max_length=20)
    school_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    pta_dues = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    computer_levy = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    exam_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    canteen_type = models.CharField(max_length=10, choices=[('terminal', 'Per Term')], default='terminal', blank=True)
    canteen_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    other_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    development_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    boarding_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hostel_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    is_published = models.BooleanField(default=False)
    
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('school', 'stage', 'term', 'academic_year')  
        ordering = ['-academic_year', 'term', 'stage']

    def save(self, *args, **kwargs):
        self.total_amount = (
            (self.school_fees or 0) + 
            (self.pta_dues or 0) + 
            (self.boarding_fee or 0) + 
            (self.hostel_fee or 0) + 
            (self.development_fee or 0) + 
            (self.computer_levy or 0) + 
            (self.exam_fees or 0) + 
            (self.canteen_amount or 0) +
            (self.other_fees or 0)
        )
        super().save(*args, **kwargs)

    def __str__(self):
        stage = self.get_stage_display() if hasattr(self, "get_stage_display") else self.stage
        return f"{stage} - {self.term} {self.academic_year} - GHS {self.total_amount}"


class FeePayment(models.Model):
    """Individual payment records"""
    PAYMENT_TYPES = [
        ('all', 'Full Payment'),
        ('school_fees', 'School Fees'),
        ('pta_dues', 'PTA Dues'),
        ('computer_levy', 'Computer Levy'),
        ('exam_fees', 'Exam Fees'),
        ('canteen', 'Canteen'),
        ('other_fees', 'Other Fees'),
    ]

    fee = models.ForeignKey('accounts.StudentFee', on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    payment_type = models.CharField(max_length=50, default='all')  # bump max_length to 50 for "school_fees,canteen"
    payment_date = models.DateField(auto_now_add=True)
    receipt_number = models.CharField(max_length=50, unique=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True) 
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    def save(self, *args, **kwargs):
        if not self.receipt_number:
            year = timezone.now().year
            count = FeePayment.objects.filter(recorded_at__year=year).count() + 1
            self.receipt_number = f'RCP-{year}-{count:04d}'
        super().save(*args, **kwargs)

    def get_payment_types_display(self):
        """Convert 'school_fees,canteen' to 'School Fees, Canteen'"""
        choices_dict = dict(self.PAYMENT_TYPES)
        types = [t.strip() for t in self.payment_type.split(',') if t.strip()]
        return ', '.join([choices_dict.get(t, t) for t in types])
    
    def __str__(self):
        return f"{self.fee.student.first_name} - GHS {self.amount} - {self.get_payment_types_display()}"

    class Meta:
        ordering = ['-payment_date']
    
    # models.py
class SchoolSetting(models.Model):

    school = models.OneToOneField(
        School,
        on_delete=models.CASCADE,
        related_name="settings",
        null=True,
        blank=True
    )
    ATTENDANCE_MODE_CHOICES = [
        ('subject', 'Subject Teacher'),
        ('class_teacher', 'Class Teacher'),
    ]

    attendance_mode = models.CharField(
        max_length=20,
        choices=ATTENDANCE_MODE_CHOICES,
        default='subject'
    )


    # Teacher attendance settings

    teacher_reporting_time = models.TimeField(
        default="07:30"
    )

    teacher_closing_time = models.TimeField(
        default="15:30"
    )

    late_after_minutes = models.PositiveIntegerField(
        default=15
    )


    class_score_total = models.IntegerField(default=50)
    exam_score_total = models.IntegerField(default=50)
    email = models.EmailField(blank=True, null=True)  
    gps_address = models.CharField(max_length=255, blank=True, null=True)  # add this
    academic_year_start_month = models.IntegerField(
        default=9,
        help_text="Month academic year starts. 1=Jan, 8=Aug, 9=Sep"
    )
    
    class Meta:
        verbose_name_plural = "School Settings"
    
    def __str__(self):
        return f"Academic year starts month {self.academic_year_start_month}"
    
    def save(self, *args, **kwargs):
        if not self.pk and SchoolSetting.objects.exists():
            raise Exception("Only one SchoolSetting record allowed")
        return super().save(*args, **kwargs)




    # -------------------------------
# TermSetting - ADD THIS
# -------------------------------
class TermSetting(models.Model):
    session = models.CharField(max_length=9) # 2024/2025
    term = models.CharField(max_length=20) # First Term
    ca_total = models.IntegerField(default=40)
    exam_total = models.IntegerField(default=60)
    is_active = models.BooleanField(default=True)
    class Meta:
        unique_together = ['session', 'term']

    def __str__(self):
        return f"{self.session} - {self.term}"


WEEKDAY_CHOICES = [
    (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), 
    (3, 'Thursday'), (4, 'Friday')
]

class Timetable(models.Model):
    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True) 
    teacher = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    related_name='timetables',
    null=True,
    blank=True
)
    school_class = models.ForeignKey('SchoolClass', on_delete=models.CASCADE, related_name='timetables')
    subject = models.ForeignKey(
    'Subject',
    on_delete=models.CASCADE,
    related_name='timetables',
    null=True,
    blank=True
)
    weekday = models.IntegerField(choices=WEEKDAY_CHOICES)
    period = models.CharField(max_length=50, blank=True, null=True)  
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    break_time = models.ForeignKey(
    'Break',
    on_delete=models.CASCADE,
    related_name='timetables',
    null=True,
    blank=True
)
    def save(self, *args, **kwargs): 
        if not self.school and self.school_class_id:
            self.school = self.school_class.school
        super().save(*args, **kwargs)
    

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['teacher', 'weekday', 'start_time', 'end_time'], 
                name='unique_teacher_time'
            ),

        ]
        ordering = ['weekday', 'period']

    def __str__(self):
        time_range = ""
        if self.start_time and self.end_time:
            time_range = f" {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')}"
        return f"{self.teacher.get_full_name()} - {self.subject.name} - {self.school_class.name} ({self.get_weekday_display()}{time_range})"

class Break(models.Model):

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE
    )

    stage = models.CharField(
        max_length=20,
        choices=STAGE_CHOICES
    )

    name = models.CharField(
        max_length=100
    )

    start_time = models.TimeField()

    end_time = models.TimeField()


    class Meta:

        constraints = [
            models.UniqueConstraint(
                fields=[
                    'school',
                    'stage',
                    'name',
                    'start_time',
                    'end_time'
                ],
                name='unique_school_stage_break'
            )
        ]

        ordering = [
            'stage',
            'start_time'
        ]


    def __str__(self):
        return f"{self.stage} - {self.name} ({self.start_time} - {self.end_time})"


class AcademicCalendar(models.Model):
    TERM_CHOICES = [
        ('term1', 'First Term'),
        ('term2', 'Second Term'),
        ('term3', 'Third Term'),
    ]

    TYPE_CHOICES = [
    ('public_holiday', 'Public Holiday'),
    ('midterm_exams', 'Mid-Term Exams'), 
    ('midterm_holiday', 'Mid-Term Break'), 
    ('revision_week', 'Revision Week'), 
    ('endterm_exams', 'End of Term Exams'), 
    ('vacation', 'Long Vacation'), 
    ('closing', 'School Closing'), 
    ('opening', 'School Re-Opening'),
    ('sports_day', 'Sports Day'),
    ('custom', 'Other School Event'), 
]
    
    
    
    name = models.CharField(max_length=100, help_text="e.g. Christmas Vacation")
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    term = models.CharField(max_length=10, choices=TERM_CHOICES, null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    academic_year = models.ForeignKey(
    'AcademicYear',
    on_delete=models.SET_NULL,
    null=True,
    blank=True
)
    affects_timetable = models.BooleanField(default=True, help_text="Uncheck if classes still run")
    
    class Meta:
        ordering = ['start_date']
        verbose_name = "Academic Calendar Event"
        verbose_name_plural = "Academic Calendar Events"
        unique_together = ('school', 'name', 'start_date', 'academic_year')
        
    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("End date cannot be before start date")
    
    def __str__(self):
        return f"{self.name} - {self.start_date}"
    

    # -------------------------------
# Expenses - ADD THIS
# -------------------------------
class Expense(models.Model):
    CATEGORY_CHOICES = [
        ('salary', 'Staff Salary'),
        ('utilities', 'Utilities - Light/Water'),
        ('supplies', 'Teaching Supplies'),
        ('maintenance', 'Maintenance/Repairs'),
        ('transport', 'Transport/Fuel'),
        ('food', 'Feeding Program'),
        ('other', 'Other'),
    ]
    
    TERM_CHOICES = [
        ('term1', 'First Term'),
        ('term2', 'Second Term'),
        ('term3', 'Third Term'),
    ]
    
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='expenses')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expense_date = models.DateField(default=timezone.now)
    
    # Add these 2 fields below
    term = models.CharField(max_length=20, choices=TERM_CHOICES)
    academic_year = models.CharField(max_length=20)  
    
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    receipt_number = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    receipt_file = models.FileField(upload_to='expense_receipts/', blank=True, null=True)  
    verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='verified_expenses')
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-academic_year', '-term', '-expense_date']

    def __str__(self):
        return f"{self.get_category_display()} - GHS {self.amount} - {self.expense_date}"


class CanteenPayment(models.Model):
    student_fee = models.ForeignKey(StudentFee, on_delete=models.CASCADE, related_name='canteen_payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(auto_now_add=True)
    recorded_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True)
    
    def __str__(self):
        return f"{self.student_fee.student.first_name} - GHS {self.amount} on {self.date}"


class PaymentTransaction(models.Model):
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE, related_name='student_transactions')
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='payment_transactions', null=True, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_types = models.ManyToManyField('PaymentType', blank=True, verbose_name='PAYMENT TYPE')
    receipt_number = models.CharField(max_length=50, unique=True, blank=True, null=True)
    transaction_id = models.CharField(max_length=100, blank=True, null=True, help_text="Parent's bank/momo reference")  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE) 
    payment_method = models.CharField(
        max_length=20, 
        choices=[
            ('cash', 'Cash'),
            ('momo', 'Mobile Money'),
            ('bank', 'Bank Transfer'),
            ('paystack', 'Paystack'),
            ('hubtel', 'Hubtel'),
        ],
        default='cash'
    )
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recorded_transactions'
    )
    # === ADMIN MONITORING ===
    is_voided = models.BooleanField(default=False)
    voided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='voided_transactions')
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)

    def get_payment_types_display(self):
        mapping = {
            'school_fees': 'School Fees',
            'pta_dues': 'PTA Dues',
            'computer_levy': 'Computer Levy',
            'exam_fees': 'Exam Fees',
            'boarding_fee': 'Boarding Fee',
            'hostel_fee': 'Hostel Fee',
            'development_fee': 'Development Fee',
            'canteen': 'Canteen',
            'other_fees': 'Other Fees',
        }
        types = self.payment_types.values_list('name', flat=True)
        if not types:
            return "—"
        return ", ".join(types)

    def save(self, *args, **kwargs):

        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and not self.receipt_number:
            year = timezone.now().year
            random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
            self.receipt_number = f"RCP-{year}-{self.id:03d}-{random_code}"
            super().save(update_fields=['receipt_number'])

    def __str__(self):
        return f"{self.student} - GHS {self.total_amount}"

class FeeAuditLog(models.Model):
    ACTION_CHOICES = [
        ('COLLECTED', 'Collected'),
        ('VOIDED', 'Voided'),
        ('EDITED', 'Edited'),
    ]
    transaction = models.ForeignKey(PaymentTransaction, on_delete=models.CASCADE, related_name='audit_logs')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    done_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    details = models.TextField(blank=True, help_text="E.g. Collected GHS 500 for Kofi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action} - {self.transaction.receipt_number} - {self.created_at.date()}"


class PendingPayment(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )
    
    PAYMENT_METHOD_CHOICES = (
        ('bank', 'Bank Transfer'),
        ('momo', 'Mobile Money'),
    )
    
    # Who & What
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='pending_payments',
        limit_choices_to={'role': 'student'}
    )
    student_fee = models.ForeignKey(
        'StudentFee', 
        on_delete=models.CASCADE,
        related_name='pending_payments'
    )
    term = models.ForeignKey('Term', on_delete=models.CASCADE)
    academic_year = models.ForeignKey('AcademicYear', on_delete=models.CASCADE)
    
    # Payment Details
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES)
    reference_number = models.CharField(max_length=100, help_text="Bank txn ID or MoMo txn ID") 
    paid_at = models.DateTimeField(help_text="Date/time parent made the payment")
    
    # Proof
    proof_image = models.ImageField(upload_to='payment_proofs/%Y/%m/', help_text="Upload screenshot or photo of receipt")
    
    # Status & Tracking
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_payments',
        limit_choices_to={'role': 'parent'}
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    
    # Accountant Action
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='verified_payments',
        limit_choices_to={'role__in': ['accountant', 'bursar', 'admin']}
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)
    
    # Link to final transaction once approved
    payment_transaction = models.OneToOneField(
        'PaymentTransaction',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pending_payment_source'
    )
    
    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Pending Payment'
        verbose_name_plural = 'Pending Payments'
    
    def __str__(self):
        return f"{self.student.student_number} - GHS {self.amount} - {self.get_status_display()}"
    
    @property
    def school(self):
        return self.student.school


class PendingPaymentItem(models.Model):
    pending_payment = models.ForeignKey(
        PendingPayment,
        on_delete=models.CASCADE,
        related_name='items'
    )

    fee_name = models.CharField(max_length=100)

    paid_field = models.CharField(
        max_length=100,
        help_text="StudentFee paid field to update after approval"
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    def __str__(self):
        return f"{self.fee_name}: GHS {self.amount}"


class PaymentItem(models.Model):
    transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.CASCADE,
        related_name='items'
    )

    payment_type = models.ForeignKey(
        'PaymentType',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    fee_name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.fee_name}: GHS {self.amount}"


class PaymentType(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='payment_types',
        null=True,
        blank=True
    )

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ('school', 'code')

    def __str__(self):
        return self.name


class ContinuousAssessment(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    score = models.FloatField(default=0)

    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)


class StudentRemark(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    student = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'student'})
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    class_teacher_remark = models.TextField(blank=True, null=True)
    headteacher_remark = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['student', 'term']
        verbose_name_plural = "Student Remarks"
    
    def __str__(self):
        term_name = self.term.get_term_number_display() if self.term else 'No Term'
        year = self.term.academic_year if self.term else 'No Year'
        student_name = self.student.get_full_name() or self.student.username
        return f"{student_name} - {term_name} {year}"


# ADD THIS AT THE TOP OF THE FILE, BEFORE YOUR CLASS
PRIORITY_CHOICES = [
    ('low', 'Info Only - Small banner at top'),
    ('medium', 'Important - Popup, can dismiss'),
    ('high', 'Critical - Must acknowledge to continue'),
]

class Announcement(models.Model):
    # This stores the checkboxes you ticked, e.g. ["student", "parent"]
    target_roles = models.JSONField(default=list, blank=True, help_text="Stores ticked roles")
    
    school = models.ForeignKey('accounts.School', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    message = models.TextField()
    
    target_class = models.ForeignKey(
        'accounts.SchoolClass', 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        help_text="Select class if targeting specific class only"
    )
    
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expiry_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    priority = models.CharField(
        max_length=10, 
        choices=PRIORITY_CHOICES, 
        default='low',
        help_text="How urgently should users see this?"
    )
    action_url = models.URLField(
        blank=True, 
        null=True,
        help_text="Optional link"
    )
    acknowledged_by = models.ManyToManyField(
        'accounts.User',
        blank=True,
        related_name='acknowledged_announcements'
    )

    def __str__(self):
        return f"{self.title} - {self.target_roles}"

    def is_expired(self):
        if self.expiry_date:
            return timezone.now().date() > self.expiry_date
        return False

    class Meta:
        ordering = ['-created_at']

class AnnouncementRead(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('announcement', 'user')


class ParentStudentLink(models.Model):
    parent = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'parent'},
        related_name='student_links'
    )

    student = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'student'},
        related_name='parent_links'
    )

    relationship = models.CharField(
        max_length=50,
        blank=True,
        null=True
    )  # e.g. Father, Mother, Guardian

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('parent', 'student')

    def __str__(self):
        return f"{self.parent.get_full_name()} → {self.student.get_full_name()}"


class Notification(models.Model):
    title = models.CharField(max_length=200)
    message = models.TextField()
    link = models.URLField(blank=True, null=True)
    target_group = models.CharField(max_length=100, default='All Users')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.title

# This second table is to track who read
class NotificationRecipient(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.notification.title}"


class SmsLog(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True)
    phone = models.CharField(max_length=20)
    message = models.TextField()
    status = models.CharField(max_length=100) # TEST or SENT or FAILED
    provider = models.CharField(max_length=20, default='hubtel')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.phone} - {self.status} - {self.created_at.date()}"


class TeacherAttendance(models.Model):
    STATUS_CHOICES = [
        ('P', 'Present'),
        ('L', 'Late'),
        ('A', 'Absent'),
        ('LV', 'Leave'),
        ('S', 'Sick'),
        ('E', 'Excused'), 
    ]

    school = models.ForeignKey(
        'School',
        on_delete=models.CASCADE,
        related_name='teacher_attendances'
    )

    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'teacher'},
        related_name='teacher_attendance_records'
    )

    date = models.DateField(default=timezone.localdate)

    status = models.CharField(
        max_length=2,
        choices=STATUS_CHOICES,
        default='P'
    )

    time_in = models.TimeField(
        null=True,
        blank=True
    )

    time_out = models.TimeField(
        null=True,
        blank=True
    )

    marked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marked_teacher_attendance'
    )
    record_source = models.CharField(
        max_length=10,
        choices=[
            ("self", "Self Check-in"),
            ("staff", "Staff Marked"),
        ],
        default="staff"
    )

    note = models.CharField(
        max_length=200,
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('teacher', 'date')
        ordering = ['-date', 'teacher__last_name', 'teacher__first_name']
        verbose_name = "Teacher Attendance"
        verbose_name_plural = "Teacher Attendances"

    def __str__(self):
        return f"{self.teacher.get_full_name()} - {self.get_status_display()} - {self.date}"

    @property
    def is_present(self):
        return self.status in ['P', 'L']

    @property
    def worked_today(self):
        return self.time_in is not None
