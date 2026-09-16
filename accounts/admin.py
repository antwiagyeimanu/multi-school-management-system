from urllib import request

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm
from.models import School, SchoolClass, Subject, TeacherSubjectClass, User, Fee, Result, StudentSubjectClass, AcademicYear
from .models import Term
from .models import StudentFee, FeePayment
from .models import AttendanceSession, AttendanceRecord
from .models import TermSetting
from .models import Timetable
from .models import  SystemSettings, AcademicCalendar
from .models import FeeStructure
from .models import PaymentTransaction
from .forms import FeeStructureForm
from django.db.models import F
from .models import SmsLog  
from .models import SchoolSetting
print([f.name for f in SchoolSetting._meta.get_fields()])
from .models import StudentRemark
from .models import PaymentItem
from .models import HeadmasterPermission
from .models import Announcement, Notification, NotificationRecipient, AnnouncementRead, TeacherAttendance, Break
from.models import FeeStructure, DynamicFeeStructureItem,  DynamicStudentFeeItem, DynamicFeeStructure, HeadmasterPermission


# -------------------------------
# This fixes the empty School dropdown
# -------------------------------
class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + (
            "email",
            "role",
            "school",
            "title",
            "first_name",
            "middle_name",
            "last_name",
            "photo",
            "gender",
            "date_of_birth",
            "nationality",
            "phone",
            "religion",
            "school_class",
            "admission_date",
            "is_class_teacher",
            "class_teacher_of",
            "parent_guardian_name",
            "parent_guardian_phone",
            "parent",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "qualification",
            "department",
            "national_id_card",
            "occupation",
            "address",
            "relationship_type",
            "conduct",
            "can_login",
        )


# -------------------------------
# Inline for Student Subjects
# -------------------------------
class StudentSubjectInline(admin.TabularInline):
    model = StudentSubjectClass
    extra = 1
    fk_name = "student"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)

        if obj and obj.school:
            formset.form.base_fields["subject"].queryset = Subject.objects.filter(
                school=obj.school
            )

            formset.form.base_fields["school_class"].queryset = (
                SchoolClass.objects.filter(school=obj.school)
            )

            formset.form.base_fields["school"].queryset = School.objects.filter(
                id=obj.school.id
            )

        return formset


# -------------------------------
# Custom UserAdmin
# -------------------------------
class UserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm
    readonly_fields = ('school_display',)

    def school_display(self, obj):
        return obj.school.name if obj.school else "-"

    school_display.short_description = "School"

    def full_name(self, obj):
        return obj.get_full_name() or obj.username

    full_name.short_description = "Full Name"

    list_display = (
        'school_display',
        'username',
        'full_name',
        'role',
        
        'staff_id',
        'student_number',
        'phone',
        'is_active',
    )
    fieldsets = (

        ('School', {'fields': (
            'school_display',
        )}),

        ('Basic Information', {'fields': (
            'photo', 'title', 'first_name', 'middle_name', 'last_name',
            'gender', 'date_of_birth', 'nationality', 'religion', 'phone',
        )}),

        ('Account Information', {'fields': (
            'username', 'email', 'password', 'role',
            'can_login', 'is_active',
        )}),

        ('School Information', {'fields': (
            'staff_id', 'student_number', 'school_class', 'admission_date',
            'is_class_teacher', 'class_teacher_of',
        )}),

        ('Parent / Guardian Information', {'fields': (
            'parent_guardian_name', 'parent_guardian_phone', 'parent',
        )}),
        ('Emergency Contact', {'fields': (
            'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_relationship',
        )}),

        ('Additional Information', {'fields': (
            'qualification', 'department', 'national_id_card', 'occupation',
            'address', 'relationship_type', 'conduct',
        )}),

        ('Permissions', {'fields': (
            'is_staff', 'is_superuser', 'groups', 'user_permissions',
        )}),
    )

    add_fieldsets = (
        ('Basic Information', {
            'classes': ('wide',),
            'fields': (
                'photo',
                'title',
                'first_name',
                'middle_name',
                'last_name',
                'gender',
                'date_of_birth',
                'nationality',
                'phone',
                'religion',
            ),
        }),

        ('Account Information', {
            'classes': ('wide',),
            'fields': (
                'username',
                'email',
                'password1',
                'password2',
                'role',
                'school',
                'can_login',
            ),
        }),

        ('School Information', {
            'classes': ('wide',),
            'fields': (
                'school_class',
                'admission_date',
                'is_class_teacher',
                'class_teacher_of',
            ),
        }),

        ('Parent / Guardian Information', {
            'classes': ('wide',),
            'fields': (
                'parent_guardian_name',
                'parent_guardian_phone',
                'parent',
            ),
        }),

        ('Emergency Contact', {
            'classes': ('wide',),
            'fields': (
                'emergency_contact_name',
                'emergency_contact_phone',
                'emergency_contact_relationship',
            ),
        }),

        ('Additional Information', {
            'classes': ('wide',),
            'fields': (
                'qualification',
                'department',
                'national_id_card',
                'occupation',
                'address',
                'relationship_type',
                'conduct',
            ),
        }),
    )

    inlines = [StudentSubjectInline]

    def get_inlines(self, request, obj=None):
        if obj and obj.role == 'student':
            return [StudentSubjectInline]
        return []


    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        if obj and obj.school:

            if "school_class" in form.base_fields:
                form.base_fields["school_class"].queryset = SchoolClass.objects.filter(
                    school=obj.school
                )

            if "class_teacher_of" in form.base_fields:
                form.base_fields["class_teacher_of"].queryset = SchoolClass.objects.filter(
                    school=obj.school
                )

            if "parent" in form.base_fields:
                form.base_fields["parent"].queryset = User.objects.filter(
                    school=obj.school, role="parent"
                )

        return form


# -------------------------------
# Register models
# -------------------------------
admin.site.register(User, UserAdmin)
@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "school")
    list_filter = ("school",)
    search_fields = ("code", "name")

    fields = ("code", "name", "school_display")

    readonly_fields = ("school_display",)

    def school_display(self, obj):
        return obj.school.name if obj.school else "-"

    school_display.short_description = "School"


admin.site.register(TeacherSubjectClass)
admin.site.register(Fee)
admin.site.register(StudentSubjectClass)
admin.site.register(AttendanceSession)


# -------------------------------
# Result Admin - For entering Class + Exam scores
# -------------------------------


@admin.register(Result)
class ResultAdmin(admin.ModelAdmin):
    list_display = ['student', 'subject', 'term', 'academic_year', 'exam_score', 'status']
    list_filter = ['term', 'academic_year', 'status', 'subject']
    list_editable = ['status']
    search_fields = ['student__username', 'student__first_name', 'student__last_name', 'subject__name']
    readonly_fields = []


# === INLINES FIRST ===
class DynamicFeeStructureItemInline(admin.TabularInline):
    model = DynamicFeeStructureItem
    extra = 1

    fields = (
        "school_display",
        "payment_type",
        "name",
        "amount",
    )

    readonly_fields = ("school_display",)

    def school_display(self, obj):
        if obj and obj.school:
            return obj.school.name
        if obj and obj.fee_structure:
            return obj.fee_structure.school.name
        return "-"

    school_display.short_description = "School"

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)

        for instance in instances:
            instance.school = formset.instance.school
            instance.save()

        formset.save_m2m()


class DynamicStudentFeeItemInline(admin.TabularInline):
    model = DynamicStudentFeeItem
    extra = 0


# === ADMINS ===
@admin.register(DynamicFeeStructure)
class DynamicFeeStructureAdmin(admin.ModelAdmin):
    list_display = [
        "school",
        "stage",
        "term",
        "academic_year",
        "total_amount",
        "is_published",
    ]

    list_filter = ["school", "stage", "term", "academic_year"]

    fields = (
        "school_display",
        "stage",
        "term",
        "academic_year",
        "is_published",
    )

    readonly_fields = ("school_display",)

    inlines = [DynamicFeeStructureItemInline]

    def school_display(self, obj):
        return obj.school.name if obj.school else "-"

    school_display.short_description = "School"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        if obj and obj.school:
            form.base_fields["term"].queryset = Term.objects.filter(school=obj.school)

            form.base_fields["academic_year"].queryset = AcademicYear.objects.filter(
                school=obj.school
            )

        return form


@admin.register(DynamicFeeStructureItem)
class DynamicFeeStructureItemAdmin(admin.ModelAdmin):
    list_display = ["school", "fee_structure", "name", "amount"]
    list_filter = ["school"]


@admin.register(StudentFee)
class StudentFeeAdmin(admin.ModelAdmin):

    def school_display(self, obj):
        return (
            obj.student.school.name
            if obj and obj.student and obj.student.school
            else "-"
        )

    school_display.short_description = "School"
    list_display = [
        "student",
        "term",
        "total_amount",
        "get_amount_paid",
        "get_balance",
        "due_date",
    ]
    readonly_fields = [
        "school_display",
        "total_amount",
        "get_balance",
        "get_amount_paid",
    ]
    fields = ["school_display", "student", "term", "total_amount", "due_date"]
    list_filter = ["term", "due_date"]
    search_fields = ["student_first_name", "student_last_name"]
    inlines = [DynamicStudentFeeItemInline]

    def get_balance(self, obj):
        try:
            return f"GHS {obj.balance()}"
        except:
            return "GHS 0"

    get_balance.short_description = "Amount Due"

    def get_amount_paid(self, obj):
        try:
            return f"GHS {obj.amount_paid()}"
        except:
            return "GHS 0"

    get_amount_paid.short_description = "Amount Paid"


@admin.register(FeePayment) 
class FeePaymentAdmin(admin.ModelAdmin):
    list_display = ['fee', 'amount', 'payment_date', 'receipt_number']
    list_filter = ['payment_date']
    search_fields = ['fee__student__first_name', 'receipt_number']


# -------------------------------
# Term Admin - For setting academic term dates
# -------------------------------
@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = (
        "school",
        "academic_year",
        "term_number",
        "start_date",
        "end_date",
        "next_term_begins",
    )

    list_filter = ("school", "academic_year", "term_number")
    search_fields = ("academic_year__name", "school__name")

    fields = (
        "school_display",
        "term_number",
        "academic_year",
        "start_date",
        "end_date",
        "days_opened",
        "next_term_begins",
        "sba_total",
        "ca_total",
        "exam_total",
        "is_active",
        
    )

    readonly_fields = ("school_display",)

    def school_display(self, obj):
        return obj.school.name if obj.school else "-"

    school_display.short_description = "School"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        if obj and obj.school:
            form.base_fields["academic_year"].queryset = AcademicYear.objects.filter(
                school=obj.school
            )

        return form


@admin.register(TermSetting)
class TermSettingAdmin(admin.ModelAdmin):
    list_display = ['term', 'ca_total', 'exam_total']
    search_fields = ['term__term']


@admin.register(Timetable)
class TimetableAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'subject', 'school_class', 'weekday', 'period']
    list_filter = ['weekday', 'school_class', 'teacher']
    search_fields = ['teacher__first_name', 'teacher__last_name', 'school_class__name']


@admin.register(AcademicCalendar)
class AcademicCalendarAdmin(admin.ModelAdmin):
    list_display = ['name', 'event_type', 'start_date', 'end_date', 'term', 'academic_year']
    list_filter = ['event_type', 'term', 'academic_year']
    date_hierarchy = 'start_date'
    search_fields = ['name']
    ordering = ['start_date']

    fieldsets = (
        ('Event Info', {
            'fields': ('name', 'event_type', 'academic_year', 'term')
        }),
        ('Dates', {
            'fields': ('start_date', 'end_date'),
            'description': 'For single-day events like Christmas, set start_date = end_date'
        }),
        ('Options', {
            'fields': ('affects_timetable',),
            'description': 'Leave checked. Uncheck only if classes still run during this event'
        }),
    )


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('receipt_number', 'student', 'total_amount', 'amount_paid', 'get_payment_types_display', 'created_at')
    search_fields = ('student__first_name', 'student__last_name')
    list_filter = ('created_at',)
    filter_horizontal = ('payment_types',)
    
    actions = ['mark_as_fully_paid']

    def get_payment_types_display(self, obj):
        return ", ".join(obj.payment_types.values_list('name', flat=True))
    get_payment_types_display.short_description = 'PAYMENT TYPE'
    
    # This needs to be indented inside the class
    def mark_as_fully_paid(self, request, queryset):
        updated = queryset.update(amount_paid=F('total_amount'))
        self.message_user(request, f'{updated} transactions marked as fully paid.')
    mark_as_fully_paid.short_description = "Mark selected as fully paid"


@admin.register(SchoolSetting)
class SchoolSettingAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Academic Settings', {
            'fields': ('class_score_total', 'exam_score_total', 'academic_year_start_month')
        }),
        ('Contact Info', {
            'fields': ('email', 'gps_address')
        }),
    )

    def has_add_permission(self, request):
        return not SchoolSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class SchoolClassInline(admin.TabularInline):
    model = SchoolClass
    extra = 0
    fields = ("name", "stage", "allows_student_login")
    show_change_link = True


class SubjectInline(admin.TabularInline):
    model = Subject
    extra = 0
    fields = ("code", "name")
    show_change_link = True


class UserInline(admin.TabularInline):
    model = User
    extra = 0
    fields = (
        "username",
        "first_name",
        "last_name",
        "role",
        "school_class",
        "student_number",
        "staff_id",
        "is_active",
    )
    show_change_link = True

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)

        if obj:
            formset.form.base_fields["school_class"].queryset = (
                SchoolClass.objects.filter(school=obj)
            )

        return formset

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)

        for instance in instances:
            instance.school = formset.instance
            instance.save()

        formset.save_m2m()


class AcademicYearInline(admin.TabularInline):
    model = AcademicYear
    extra = 0
    fields = ("name", "is_active")
    show_change_link = True


class TermInline(admin.TabularInline):
    model = Term
    extra = 0
    fields = (
        "term_number",
        "academic_year",
        "start_date",
        "end_date",
        "days_opened",
        "next_term_begins",
        "is_active",
    )
    show_change_link = True


class DynamicFeeStructureInline(admin.TabularInline):
    template = "admin/dynamic_fee_structure_inline.html"
    model = DynamicFeeStructure
    extra = 0

    fields = (
        "stage_display",
        "term_display",
        "academic_year",
        "total_amount_display",
        "due_date",
        "is_published",
    )

    readonly_fields = (
        "stage_display",
        "term_display",
        "academic_year",
        "total_amount_display",
        "due_date",
        "is_published",
    )

    show_change_link = True

    def stage_display(self, obj):
        return obj.get_stage_display() if obj else "-"

    stage_display.short_description = "Stage"

    def term_display(self, obj):
        return str(obj.term) if obj and obj.term else "-"

    term_display.short_description = "Term"

    def total_amount_display(self, obj):
        return f"GHS {obj.total_amount:,.2f}" if obj else "GHS 0.00"

    total_amount_display.short_description = "Amount"


class StudentFeeInline(admin.TabularInline):
    model = StudentFee
    extra = 0
    fields = (
        "student",
        "term",
        "due_date",
    )
    show_change_link = True

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)

        if obj:
            formset.form.base_fields["student"].queryset = User.objects.filter(
                school=obj, role="student"
            )

            formset.form.base_fields["term"].queryset = Term.objects.filter(school=obj)

        return formset

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)

        for instance in instances:
            instance.student.school = formset.instance
            instance.student.save()

            instance.term.school = formset.instance
            instance.term.save()

            instance.save()

        formset.save_m2m()


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'sms_enabled', 'sms_test_mode', 'payment_gateway']
    inlines = [SchoolClassInline, SubjectInline, UserInline, AcademicYearInline, TermInline, DynamicFeeStructureInline, StudentFeeInline]

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": (
                    "name",
                    "edition",
                    "address",
                    "phone",
                    "email",
                    "gps_address",
                    "logo",
                    "level",
                    "can_teachers_manage_students",
                    "allows_student_login",
                )
            },
        ),
        (
            "Payment Settings",
            {
                "fields": (
                    "accept_cash",
                    "accept_manual",
                    "accept_automatic",
                    "accept_momo",
                    "accept_bank",
                    "payment_gateway",
                    "momo_number",
                    "momo_name",
                    "bank_name",
                    "account_number",
                    "account_name",
                    "paystack_public_key",
                    "paystack_secret_key",
                    "hubtel_client_id",
                    "hubtel_client_secret",
                    "hubtel_merchant_account",
                    "flutterwave_public_key",
                    "flutterwave_secret_key",
                ),
            },
        ),
        (
            "📱 SMS Settings - NEW",
            {
                "fields": (
                    "sms_enabled",
                    "sms_test_mode",
                    "sms_provider",
                    "sms_sender_id",
                    "mnotify_api_key",
                    "mnotify_sender_id",
                ),
                "description": "Tick sms_enabled to show SMS checkbox in Announcement. Test Mode = FREE, only logs in SmsLog.",
            },
        ),
    )


@admin.register(StudentRemark)
class StudentRemarkAdmin(admin.ModelAdmin):
    list_display = ['student', 'get_term', 'get_academic_year', 'class_teacher_remark']
    list_filter = ['term', 'student__school_class']  # removed academic_year here
    search_fields = ['student__name', 'student__student_number']

    def get_term(self, obj):
        return obj.term.get_term_number_display() if obj.term else '-'
    get_term.short_description = 'Term'
    get_term.admin_order_field = 'term__term_number'

    def get_academic_year(self, obj):
        return obj.term.academic_year if obj.term else '-'
    get_academic_year.short_description = 'Academic Year'
    get_academic_year.admin_order_field = 'term__academic_year'


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ("name", "stage", "school", "allows_student_login")
    list_filter = ("school", "stage")
    search_fields = ("name",)

    fields = (
        "name",
        "stage",
        "allows_student_login",
        "school_display",
    )

    readonly_fields = ("school_display",)

    def school_display(self, obj):
        return obj.school.name if obj.school else "-"

    school_display.short_description = "School"


@admin.register(Announcement)
class AnnouncementsAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'priority', 'created_by','target_roles','school','created_at']
    list_filter = ['priority',  'created_at']


@admin.register(PaymentItem)
class PaymentItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'transaction', 'get_student', 'fee_name', 'amount']
    list_filter = ['fee_name']
    search_fields = ['transaction__student__user__first_name', 'transaction__student__user__last_name', 'fee_name']
    
    def get_student(self, obj):
        return obj.transaction.student
    get_student.short_description = 'Student'


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['student', 'session', 'status', 'marked_at']
    list_filter = ['status', 'session__date', 'session__school_class']
    search_fields = ['student__first_name', 'student__last_name']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('id','title','target_group','created_at')

@admin.register(NotificationRecipient)
class NotificationRecipientAdmin(admin.ModelAdmin):
    list_display = ('id','user','notification','is_read')

@admin.register(AnnouncementRead)
class AnnouncementReadAdmin(admin.ModelAdmin):
    list_display = ('id','user','announcement','read_at')

@admin.register(SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = ['school', 'phone', 'status', 'provider', 'created_at']
    list_filter = ['status', 'provider']
    search_fields = ['phone', 'message']


@admin.register(TeacherAttendance)
class TeacherAttendanceAdmin(admin.ModelAdmin):

    list_display = (
        'teacher',
        'date',
        'status',
        'time_in',
        'time_out',
        'marked_by',
        'school',
    )

    list_filter = (
        'school',
        'status',
        'date',
    )

    search_fields = (
        'teacher__first_name',
        'teacher__last_name',
        'teacher__username',
    )

    ordering = (
        '-date',
    )


@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = (
        'school',
        'stage',
        'name',
        'start_time',
        'end_time',
    )


@admin.register(HeadmasterPermission)
class HeadmasterPermissionAdmin(admin.ModelAdmin):
    pass


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ["school", "name", "is_active"]
    list_filter = ["school", "is_active"]
    search_fields = ["name", "school__name"]

    fields = (
        'school_display',
        'name',
        'start_date',
        'end_date',
        'is_active',
        
    )

    readonly_fields = ('school_display',)

    def school_display(self, obj):
        return obj.school.name if obj.school else "-"

    school_display.short_description = "School"

    def save_model(self, request, obj, form, change):
        if not obj.school:
            obj.school = request.user.school
        super().save_model(request, obj, form, change)
