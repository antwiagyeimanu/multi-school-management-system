from django.core.management.base import BaseCommand
from django.core.management import call_command
from accounts.models import User, School
import secrets
import getpass

class Command(BaseCommand):
    help = 'Setup a new school with secure admin account'

    def handle(self, *args, **options):
        call_command('migrate')
        
        # 1. Get school details from the buyer
        school_name = input("Enter School Name: ")  # St. Mary's SHS
        school_code = input("Enter School Code/Short Name: ")  # STMARYS
        
        if not School.objects.exists():
            school = School.objects.create(name=school_name, code=school_code)
            self.stdout.write(f'✓ Created school: {school_name}')
        else:
            school = School.objects.first()
            self.stdout.write(f'✓ Using existing school: {school.name}')
        
        # 2. Get REAL owner email + generate safe password
        owner_email = input("Enter Owner/Headmaster Email: ")  # head@stmarys.edu.gh
        
        if not User.objects.filter(email=owner_email).exists():
            temp_password = secrets.token_urlsafe(10)  # Random: 'kJ8mP3nQ9x'
            
            admin = User.objects.create_superuser(
                username='owner',  # Better than 'admin'
                email=owner_email, 
                password=temp_password
            )
            admin.role = 'admin'
            admin.school = school
            admin.is_password_changed = False  # Force them to change it
            admin.save()
            
            self.stdout.write(self.style.SUCCESS('✓ OWNER ACCOUNT CREATED'))
            self.stdout.write(self.style.WARNING(f'Email: {owner_email}'))
            self.stdout.write(self.style.WARNING(f'Temporary Password: {temp_password}'))
            self.stdout.write(self.style.ERROR('GIVE THIS TO OWNER. TELL THEM TO CHANGE PASSWORD ON FIRST LOGIN.'))
        else:
            self.stdout.write('✓ Owner already exists')