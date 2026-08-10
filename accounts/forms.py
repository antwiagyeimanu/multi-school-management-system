from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from accounts.models import User, Subject, SchoolClass, TeacherSubjectClass, Term, Result
import secrets, string, datetime, random
from .models import User
from .models import AcademicCalendar, AcademicYear
from.models import FeeStructure 
from .models import StudentFee  
from .models import Expense
import re
from.models import SchoolSetting
from .models import Announcement
from .models import School
from .models import PendingPayment


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'email', 'role', 'password1', 'password2')

class CustomAuthenticationForm(forms.Form):
    username_or_email = forms.CharField(label="Username or Email", widget=forms.TextInput(attrs={'autofocus': True}))
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_cache = None

    def clean(self):
        username_or_email = self.cleaned_data.get('username_or_email')
        password = self.cleaned_data.get('password')

        if username_or_email and password:
            try:
                user = User.objects.get(username=username_or_email)
            except User.DoesNotExist:
                try:
                    user = User.objects.get(email=username_or_email)
                except User.DoesNotExist:
                    raise forms.ValidationError("Invalid username/email or password")

            if not user.check_password(password):
                raise forms.ValidationError("Invalid username/email or password")

            self.user_cache = user

        return self.cleaned_data

    def get_user(self):
        return self.user_cache
    

class ResultForm(forms.ModelForm):
    class Meta:
        model = Result

        fields = [
            'exam_score'
        ]

        widgets = {
            'exam_score': forms.NumberInput(attrs={
                'placeholder': 'Score',
                'class': 'form-control',
                'step': '0.01'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        students = kwargs.pop('students', None)
        super().__init__(*args, **kwargs)
        if students is not None:
            self.fields['student'].queryset = students

TITLE_CHOICES = [
    ('', 'Select Title'),
    ('Mr', 'Mr.'), ('Mrs', 'Mrs.'), ('Miss', 'Miss'),
    ('Ms', 'Ms.'), ('Dr', 'Dr.'), ('Prof', 'Prof.'), ('Rev', 'Rev.'),
]

GENDER_CHOICES = [
    ('', 'Select Gender'),
    ('M', 'Male'), ('F', 'Female'), ('NB', 'Non-binary'),
    ('T', 'Transgender'), ('O', 'Other'), ('N', 'Prefer not to say'),
]

RELIGION_CHOICES = [
    ('', 'Select Religion'),
    ('CHRISTIAN', 'Christianity'), ('MUSLIM', 'Islam'),
    ('TRADITIONAL', 'Traditional'), ('OTHER', 'Other'), ('NONE', 'None/Prefer not to say'),
]

class TeacherForm(forms.ModelForm):
    password = forms.CharField(
        label='Password (auto-generated)',
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_password',
            'readonly': 'readonly',
            'class': 'form-control'
        }),
        help_text='Temporary password. Teacher must change on first login.'
    )
    
    # ADD THESE 3 LINES - Tell Django these are dropdowns
    title = forms.ChoiceField(choices=TITLE_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}))
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}))
    religion = forms.ChoiceField(choices=RELIGION_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}))
    is_class_teacher = forms.BooleanField(required=False, label='Make this teacher a Class Teacher')
    class_teacher_of = forms.ModelChoiceField(queryset=SchoolClass.objects.none(), required=False, empty_label="-- Select Class --")
    
    

    class Meta:
        model = User
        fields = [
            'title', 'first_name', 'middle_name', 'last_name',
            'national_id_card', 'gender', 'religion', 'phone', 
            'date_of_birth', 'nationality',
            'qualification', 'department', 'school', 
            'email', 'photo',
            'is_class_teacher', 'class_teacher_of'
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'national_id_card': forms.TextInput(attrs={'placeholder': 'GHA-123456789-1', 'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'placeholder': '0244123456', 'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'qualification': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'photo': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }
    def __init__(self, *args, **kwargs):
        school = kwargs.pop('school', None)  # <-- MUST be first line
        super().__init__(*args, **kwargs)
        
        
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['email'].required = True
        self.fields['phone'].required = True
        self.fields['national_id_card'].required = True

        if school:
            self.fields['class_teacher_of'].queryset = SchoolClass.objects.filter(school=school)
        
      
        self.fields['school'].empty_label = "Select School"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'teacher'
        
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)  
            user.is_password_changed = False
        
        if commit:
            user.save()
        return user
    

class AssignTeacherForm(forms.ModelForm):
    class Meta:
        model = TeacherSubjectClass
        fields = ['teacher', 'subject', 'school_class']





GENDER_CHOICES = [
    ('', 'Select Gender'),
    ('M', 'Male'),
    ('F', 'Female'),
    ('NB', 'Non-binary'),
    ('T', 'Transgender'),
    ('O', 'Other'),
    ('N', 'Prefer not to say'),
]

RELIGION_CHOICES = [
    ('', 'Select Religion'),
    ('CHRISTIAN', 'Christianity'),
    ('MUSLIM', 'Islam'),
    ('TRADITIONAL', 'Traditional'),
    ('OTHER', 'Other'),
    ('NONE', 'None/Prefer not to say'),
]

class StudentForm(forms.ModelForm):
    # ADD THESE 2 LINES - This tells Django these are dropdowns with options
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control'}))
    religion = forms.ChoiceField(choices=RELIGION_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = [
            'first_name', 'middle_name', 'last_name', 'email', 'phone',
            'gender', 'religion', 'date_of_birth', 'admission_date',
            'nationality', 'parent_guardian_name', 'parent_guardian_phone',
            'parent', 'school_class', 'photo',     'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'admission_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'photo': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'school_class': forms.Select(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'nationality': forms.TextInput(attrs={'class': 'form-control'}),
            'parent_guardian_name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent_guardian_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_contact_relationship': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        if self.request and hasattr(self.request, 'user'):
            school = self.request.user.school
            self.fields['parent'].queryset = User.objects.filter(role='parent', school=school)
            classes = SchoolClass.objects.filter(school=school)

            visible_ids = []

            parents_with_sections = set()

            for c in classes:

                name = c.name.strip()

               

                if len(name) > 1 and name[-1].isalpha():

                    parent_name = name[:-1].strip()

                    parent_exists = classes.filter(name=parent_name).exists()

                    if parent_exists:
                        parents_with_sections.add(parent_name)

            for c in classes:

                name = c.name.strip()

                if name in parents_with_sections:
                    continue

                visible_ids.append(c.id)

                self.fields['school_class'].queryset = SchoolClass.objects.filter(
                    school=self.request.user.school
).order_by('name')

        self.fields['parent'].empty_label = "Select Parent"
        self.fields['school_class'].empty_label = "Select Class"
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['school_class'].required = True

        # Make optional
        for field in ['middle_name', 'email', 'phone', 'parent', 'photo',
                    'date_of_birth', 'admission_date', 'nationality',
                    'parent_guardian_name', 'parent_guardian_phone',
                    'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship']:
            self.fields[field].required = False
    


class TeacherEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'title', 
            'first_name', 
            'last_name', 
            'middle_name',
            'email', 
            'phone', 
            'gender', 
            'date_of_birth',
            'nationality', 
            'religion',
            'qualification',
            'department', 
            'photo', 
            'is_active'
        ]
        widgets = {
            'title': forms.Select(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'gender': forms.Select(attrs={'class': 'form-control'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'nationality': forms.TextInput(attrs={'class': 'form-control'}),
            'religion': forms.Select(attrs={'class': 'form-control'}),
            'qualification': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'photo': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SchoolClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ['name', 'stage', 'allows_student_login']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.request and hasattr(self.request.user, 'school'):
            instance.school = self.request.user.school
        if commit:
            instance.save()
        return instance




class AddSubjectToClassForm(forms.Form):
    subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=True,
        label="Select subjects to assign to this class"
    )
    
    def __init__(self, *args, **kwargs):
        school = kwargs.pop('school', None)
        super().__init__(*args, **kwargs)
        if school:
            self.fields['subjects'].queryset = Subject.objects.filter(school=school)




class ResultUploadForm(forms.ModelForm):
    class Meta:
        model = Result
        fields = ['student', 'exam_score']

        widgets = {
            'student': forms.Select(attrs={'class': 'form-select'}),
            'exam_score': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        class_id = kwargs.pop('class_id', None)

        super().__init__(*args, **kwargs)

        if class_id:
            self.fields['student'].queryset = User.objects.filter(
                role='student',
                school_class_id=class_id
            ).order_by('last_name')

            self.fields['student'].empty_label = "Select a student"

        term = Term.objects.filter(is_active=True).first()

        if term:
            self.fields['exam_score'].widget.attrs['max'] = term.exam_total
            self.fields['exam_score'].initial = None


class TermSettingForm(forms.ModelForm):
    new_academic_year = forms.CharField(
        required=False,
        max_length=9,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 2025/2026'})
    )

    class Meta:
        model = Term
        fields = ['term_number', 'academic_year', 'new_academic_year',
                'start_date', 'end_date',
                'next_term_begins', 'ca_total', 'exam_total', 'is_active']
        widgets = {
            'term_number': forms.Select(attrs={'class': 'form-select'}),
            'academic_year': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'next_term_begins': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'ca_total': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 40'}),
            'exam_total': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 60'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['academic_year'].queryset = AcademicYear.objects.all().order_by('-name')
        self.fields['academic_year'].required = False 
        self.fields['term_number'].choices = Term.TERM_CHOICES

    def save(self, commit=True):
        new_year = self.cleaned_data.get('new_academic_year')

        instance = super().save(commit=False)

        if new_year:
            ay_obj, _ = AcademicYear.objects.get_or_create(name=new_year)
            instance.academic_year = ay_obj

        if commit:
            instance.save()

        return instance





    


    list_display = ['name', 'school']
    
    def save_model(self, request, obj, form, change):
        if not obj.school:  # only set if blank
            obj.school = request.user.school
        super().save_model(request, obj, form, change)




class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ['ca_total', 'exam_total', 'is_active']  
        widgets = {
            'ca_total': forms.NumberInput(attrs={'class': 'form-control'}),
            'exam_total': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class SchoolSettingForm(forms.ModelForm):
    class Meta:
        model = SchoolSetting
        fields = ['attendance_mode']
        widgets = {
            'attendance_mode': forms.Select(attrs={'class': 'form-select'}),
        }


class AcademicCalendarForm(forms.ModelForm):
    event_type = forms.ChoiceField(
        choices=[('', 'Select Event')] + AcademicCalendar.TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    term = forms.ChoiceField(
        choices=[('', 'Select Term')] + AcademicCalendar.TERM_CHOICES,
        required=False,  
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = AcademicCalendar
        fields = ['academic_year', 'name', 'event_type', 'term', 'start_date', 'end_date', 'affects_timetable']
        labels = {
            'affects_timetable': 'Cancel classes on these days?'
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        'start_date': forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        }),
        'end_date': forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        }),
        'affects_timetable': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    }


class AccountantForm(forms.ModelForm):
    password = forms.CharField(
        label='Password (auto-generated)',
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'id_password',
            'readonly': 'readonly',
            'class': 'form-control'
        }),
        help_text='Temporary password. Accountant must change on first login.'
    )
    
    title = forms.ChoiceField(choices=TITLE_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}))
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}))
    religion = forms.ChoiceField(choices=RELIGION_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}))

    class Meta:
        model = User
        fields = [
            'title', 'first_name', 'middle_name', 'last_name',
            'national_id_card',  # ← Correct field name
            'gender', 'religion', 'phone', 
            'date_of_birth', 'nationality',
            'qualification', 'department', 
             'email', 'photo', 'is_active' 
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'national_id_card': forms.TextInput(attrs={'placeholder': 'GHA-123456789-1', 'class': 'form-control'}),  # ← Correct
            'phone': forms.TextInput(attrs={'placeholder': '0244123456', 'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'qualification': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'photo': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['email'].required = True
        self.fields['phone'].required = True
        self.fields['national_id_card'].required = True  # ← Correct

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'accountant'
        
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)  
            user.is_password_changed = False
        
        if commit:
            user.save()  
        return user

class AccountantEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'title', 
            'first_name', 
            'middle_name',
            'last_name', 
            'email', 
            'phone', 
            'gender', 
            'date_of_birth',
            'nationality', 
            'religion',
            'qualification',
            'department', 
            'photo', 
            'is_active'
        ]
        widgets = {
            'title': forms.Select(attrs={'class': 'form-select'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'nationality': forms.TextInput(attrs={'class': 'form-control'}),
            'religion': forms.Select(attrs={'class': 'form-select'}),
            'qualification': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'photo': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    

class FeeStructureForm(forms.ModelForm):
    
    class Meta:
        model = FeeStructure
        fields = ['stage', 'term', 'academic_year', 'school_fees', 'canteen_amount',  'canteen_type', 'boarding_fee', 'hostel_fee', 
                  'development_fee', 'pta_dues',
                  'computer_levy', 'exam_fees', 'other_fees']
        widgets = {
            'stage': forms.Select(attrs={'class': 'form-select'}),
            'term': forms.Select(attrs={'class': 'form-select'}),
            'academic_year': forms.TextInput(attrs={'class': 'form-control'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),

            'school_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'canteen_type': forms.Select(attrs={'class': 'form-select'}),
            'canteen_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),

            'boarding_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'hostel_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),

            'development_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'pta_dues': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'computer_levy': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'exam_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'other_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        decimal_fields = ['school_fees', 'canteen_amount', 'canteen_type', 'boarding_fee', 'hostel_fee', 'exam_fees', 'development_fee', 'pta_dues', 'computer_levy', 'other_fees']

        for field in decimal_fields:
            value = cleaned_data.get(field)
            if value in [None, '']:
                cleaned_data[field] = 0
        return cleaned_data
    

class StudentFeeForm(forms.ModelForm):
    CANTEEN_CHOICES = [
        ('terminal', 'Per Term'),
    ]
    
    canteen_type = forms.ChoiceField(
        choices=CANTEEN_CHOICES, 
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    canteen_amount = forms.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'})
    )

    class Meta:
        model = StudentFee
        fields = [
            'school_fees', 'boarding_fee', 'hostel_fee', 'pta_dues', 'exam_fees', 
            'computer_levy', 'development_fee', 'other_fees',
            'canteen_type', 'canteen_amount'
        ]
        widgets = {
            'school_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'canteen_type': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'canteen_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'boarding_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'hostel_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'development_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'pta_dues': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'exam_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'computer_levy': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'other_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
        }



        

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['category', 'description', 'amount', 'expense_date', 'receipt_number', 'receipt_file']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'expense_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'receipt_number': forms.TextInput(attrs={'class': 'form-control'}),
            'receipt_file': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*,.pdf'}),
        }

  
class AnnouncementForm(forms.ModelForm):
    ROLE_CHOICES_FOR_ANNOUNCEMENT = [
        ('all', 'All Users - Everyone'),
        ('student', 'Students'),
        ('parent', 'Parents'),
        ('teacher', 'Teachers'),
        ('accountant', 'Accountants'),
        ('bursar', 'Bursars'),
        ('hod', 'HODs'),
        ('headmaster', 'Headmasters'),
        ('admin', 'Admins'),
        ('board_director', 'Board Directors'),
        ('support staff', 'Support Staff'), 
        ('specific class', 'Specific Class Only'),
    ]
    
    target_roles = forms.MultipleChoiceField(
        choices=ROLE_CHOICES_FOR_ANNOUNCEMENT,
        widget=forms.CheckboxSelectMultiple,
        label="Send To (tick many)"
    )

    class Meta:
        model = Announcement
        fields = ['title', 'message', 'target_roles', 'target_class', 'priority', 'expiry_date', 'action_url']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. School Closes Friday'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 6, 'placeholder': 'Type your full announcement...'}),
            'target_class': forms.Select(attrs={'class': 'form-select'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'expiry_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'action_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': '/fees/pay/ or blank'}),
        }

    # THIS IS THE MISSING PART - ADD IT
    def __init__(self, *args, **kwargs):
        school = kwargs.pop('school', None)
        super().__init__(*args, **kwargs)
        if school:
            from .models import SchoolClass
            self.fields['target_class'].queryset = SchoolClass.objects.filter(school=school)


class SchoolPaymentSettingsForm(forms.ModelForm):
    # Only override this 1 field
    hubtel_client_secret = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Enter client secret', 'autocomplete': 'new-password'}),
        required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Force Hubtel secret to be empty on page load
        if self.instance.pk:
            self.initial['hubtel_client_secret'] = ''

    def clean_hubtel_client_secret(self):
        data = self.cleaned_data['hubtel_client_secret']
        # If user left it blank, keep the old secret from database
        if data == '' and self.instance.pk:
            return self.instance.hubtel_client_secret
        return data
    
    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('accept_momo'):
            cleaned['momo_number'] = ''
            cleaned['momo_name'] = ''
        if not cleaned.get('accept_bank'):
            cleaned['bank_name'] = ''
            cleaned['account_number'] = ''
            cleaned['account_name'] = ''
        return cleaned

    class Meta:
        model = School
        fields = [
            'accept_momo',
            'accept_bank',
            'accept_cash',
            'accept_manual',
            'accept_automatic',
            'payment_gateway',
            'momo_number', 
            'momo_name',
            'bank_name', 
            'account_number', 
            'account_name',
            'paystack_public_key',
            'paystack_secret_key',
            'hubtel_client_id',
            'hubtel_client_secret',
            'hubtel_merchant_account',
            'flutterwave_public_key',
            'flutterwave_secret_key',
        ]
        widgets = {
            'accept_momo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'accept_bank': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'accept_cash': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'accept_manual': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'accept_automatic': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            
            'payment_gateway': forms.Select(attrs={'class': 'form-control', 'style': 'cursor: pointer;'}),
            'momo_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '020296XXXX'}),
            'momo_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'School Name'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'GCB'}),
            'account_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '0042954750XXXX'}),
            'account_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'School Account Name'}),
            'paystack_public_key': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'pk_live_...'}),
            'paystack_secret_key': forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'sk_live_...'}),
            'hubtel_client_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. your_client_id'}),
            'hubtel_merchant_account': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'CMA0000XXX'}),
            'flutterwave_public_key': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'FLWPUBK_...'}),
            'flutterwave_secret_key': forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'FLWSECK_...'}),
        }


class PendingPaymentForm(forms.ModelForm):

    class Meta:
        model = PendingPayment
        fields = [
            'amount',
            'payment_method',
            'reference_number',
            'paid_at',
            'proof_image'
        ]

        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'placeholder': 'Enter amount paid'
            }),

            'payment_method': forms.Select(attrs={
                'class': 'form-control'
            }),

            'reference_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Transaction ID / Reference Number'
            }),

            'paid_at': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),

            'proof_image': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*'
            })
        }

        labels = {
            'payment_method': 'Payment Method',
            'reference_number': 'Transaction ID / Reference Number',
            'paid_at': 'Date & Time of Payment',
            'proof_image': 'Upload Proof (Screenshot/Photo)'
        }


    def __init__(self, *args, **kwargs):
        school = kwargs.pop('school', None)
        super().__init__(*args, **kwargs)


        # Default labels
        self.fields['reference_number'].label = "Transaction ID / Reference Number"
        self.fields['reference_number'].widget.attrs.update({
            'placeholder': 'Enter transaction ID'
        })


        if school:

            if school.accept_momo and not school.accept_bank:

                self.fields['payment_method'].choices = [
                    ('momo', 'Mobile Money')
                ]

                self.fields['reference_number'].label = "MoMo Transaction ID"
                self.fields['reference_number'].widget.attrs.update({
                    'placeholder': 'Enter MoMo transaction ID'
                })


            elif school.accept_bank and not school.accept_momo:

                self.fields['payment_method'].choices = [
                    ('bank', 'Bank Transfer')
                ]

                self.fields['reference_number'].label = "Bank Transaction ID"
                self.fields['reference_number'].widget.attrs.update({
                    'placeholder': 'Enter bank transaction ID'
                })


            elif school.accept_bank and school.accept_momo:

                self.fields['payment_method'].choices = [
                    ('momo', 'Mobile Money'),
                    ('bank', 'Bank Transfer')
                ]

class SchoolSmsSettingsForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            'sms_enabled',
            'sms_test_mode',
            'sms_provider',
            'sms_sender_id',
            'hubtel_client_id',
            'hubtel_client_secret',
            'mnotify_api_key',
            'mnotify_sender_id',
        ]
        widgets = {
            'sms_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'sms_test_mode': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'sms_provider': forms.Select(attrs={'class': 'form-select'}),
            'sms_sender_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. ADISSCH (max 11 chars)'}),
            'hubtel_client_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Hubtel Client ID'}),
            'hubtel_client_secret': forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Hubtel Client Secret'}),
            'mnotify_api_key': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'MNotify API Key'}),
            'mnotify_sender_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'MNotify Sender ID'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        
        sms_enabled = cleaned_data.get('sms_enabled')
        sms_test_mode = cleaned_data.get('sms_test_mode')
        sms_provider = cleaned_data.get('sms_provider')
        
        sender_id = cleaned_data.get('sms_sender_id')
        hubtel_id = cleaned_data.get('hubtel_client_id')
        hubtel_secret = cleaned_data.get('hubtel_client_secret')
        mnotify_key = cleaned_data.get('mnotify_api_key')
        mnotify_sender = cleaned_data.get('mnotify_sender_id')

        # FREE MODE -> allow empty, no error
        if sms_enabled and sms_test_mode:
            return cleaned_data

        # LIVE MODE -> Must require fields!
        if sms_enabled and not sms_test_mode:
            if not sms_provider:
                self.add_error('sms_provider', 'Select SMS provider for live mode!')

            # Check Sender ID length
            if sender_id and len(sender_id) > 11:
                self.add_error('sms_sender_id', 'Max 11 characters!')

            # If provider is HUBTEL
            if sms_provider == 'hubtel':
                if not hubtel_id:
                    self.add_error('hubtel_client_id', 'Hubtel Client ID is required!')
                if not hubtel_secret:
                    self.add_error('hubtel_client_secret', 'Hubtel Secret is required!')
                if not sender_id:
                    self.add_error('sms_sender_id', 'Sender ID is required! e.g. GOLDSTAR')

            # If provider is MNOTIFY
            if sms_provider == 'mnotify':
                if not mnotify_key:
                    self.add_error('mnotify_api_key', 'MNotify API Key is required!')
                if not mnotify_sender:
                    self.add_error('mnotify_sender_id', 'MNotify Sender ID is required!')

        return cleaned_data