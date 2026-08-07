from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm
from.models import School, SchoolClass, Subject, TeacherSubjectClass, User, Fee, Result, StudentSubjectClass
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
from .models import SmsLog  # add at top
from .models import SchoolSetting
print([f.name for f in SchoolSetting._meta.get_fields()])
from .models import StudentRemark
from .models import PaymentItem
from .models import Announcement, Notification, NotificationRecipient, AnnouncementRead, TeacherAttendance, Break














# -------------------------------
# This fixes the empty School dropdown
# -------------------------------
class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ('email', 'role', 'school', 'school_class')

# -------------------------------
# Inline for Student Subjects
# -------------------------------
class StudentSubjectInline(admin.TabularInline):
    model = StudentSubjectClass
    extra = 1
    fk_name = 'student'

# -------------------------------
# Custom UserAdmin
# -------------------------------
class UserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm

    list_display = (
        'username', 'email', 'first_name', 'last_name', 'role',
        'gender', 'religion', 'student_number', 'is_active'
    ) 

    fieldsets = (
        (None, {'fields': ('username', 'email', 'password', 'role', 'school', 'school_class')}),
        ('Personal Info', {'fields': (
            'first_name', 'middle_name', 'last_name', 'student_number',
            'gender', 'date_of_birth', 'religion', 'nationality', 'phone'
        )}), 
        ('Guardian Info', {'fields': (
            'parent_guardian_name', 'parent_guardian_phone', 'parent'
        )}), 
        ('School Info', {'fields': ('admission_date', 'is_class_teacher', 'class_teacher_of',)}), 
        ('Permissions', {'fields': ('is_staff', 'is_active', 'is_superuser', 'groups', 'user_permissions')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username', 'email', 'password1', 'password2', 'role', 'school', 'school_class',
                'first_name', 'middle_name', 'last_name', 'gender', 'date_of_birth',
                'religion', 'nationality', 'parent_guardian_name', 'parent_guardian_phone'
            ),
        }),
    )

    inlines = [StudentSubjectInline]

    def get_inlines(self, request, obj=None):
        if obj and obj.role == 'student':
            return [StudentSubjectInline]
        return []
# -------------------------------
# Register models
# -------------------------------
admin.site.register(User, UserAdmin)
admin.site.register(Subject)
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
    


@admin.register(StudentFee)
class StudentFeeAdmin(admin.ModelAdmin):
    list_display = ['student', 'term', 'total_amount', 'get_amount_paid', 'get_balance', 'due_date']
    readonly_fields = ['total_amount', 'get_balance', 'get_amount_paid']  # make total readonly
    fields = [
        'student', 
        'term', 
        'school_fees', 
        'canteen_amount', 
        'boarding_fee',
        'hostel_fee',
        'pta_dues', 
        'development_fee',
        'computer_levy', 
        'exam_fees', 
        'other_fees', 
        'total_amount', 
        'due_date'
    ]
    list_filter = ['term',  'due_date']
    search_fields = ['student__first_name', 'student__last_name']
    
    def get_balance(self, obj):
        try:
            return f"GHS {obj.balance()}"
        except:
            return "GHS 0"
    get_balance.short_description = 'Amount Due'
    
    def get_amount_paid(self, obj):
        try:
            return f"GHS {obj.amount_paid()}"
        except:
            return "GHS 0"
    get_amount_paid.short_description = 'Amount Paid'




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
    list_display = ('school', 'academic_year', 'term_number', 'start_date', 'end_date', 'next_term_begins')
    list_filter = ('school', 'academic_year', 'term_number')
    search_fields = ('academic_year__name', 'school__name')


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


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    form = FeeStructureForm
    list_display = ['school', 'term', 'academic_year', 'total_amount', 'is_active']
    list_filter = ['school', 'term', 'academic_year', 'is_active']
    search_fields = ['school__name']




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
    
@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'sms_enabled', 'sms_test_mode', 'payment_gateway']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'address', 'phone', 'email', 'gps_address', 'logo', 'level', 'can_teachers_manage_students', 'allows_student_login')
        }),
        ('Payment Settings', {
            'fields': ('accept_cash', 'accept_manual', 'accept_automatic', 'accept_momo', 'accept_bank', 'payment_gateway', 'momo_number', 'momo_name', 'bank_name', 'account_number', 'account_name', 'paystack_public_key', 'paystack_secret_key', 'hubtel_client_id', 'hubtel_client_secret', 'hubtel_merchant_account', 'flutterwave_public_key', 'flutterwave_secret_key'),
        }),
        ('📱 SMS Settings - NEW', {
            'fields': ('sms_enabled', 'sms_test_mode', 'sms_provider', 'sms_sender_id', 'mnotify_api_key', 'mnotify_sender_id'),
            'description': 'Tick sms_enabled to show SMS checkbox in Announcement. Test Mode = FREE, only logs in SmsLog.'
        }),
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





class SchoolClassAdmin(admin.ModelAdmin):
    pass
admin.site.register(SchoolClass, SchoolClassAdmin)



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