import os
import django
from django.db import connection

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sms.settings')
django.setup()

fields = ['school_fees', 'pta_dues', 'computer_levy', 'exam_fees', 'other_fees', 'total_amount']

with connection.cursor() as cursor:
    for f in fields:
        cursor.execute(f"""
            UPDATE accounts_feestructure 
            SET {f} = 0 
            WHERE TRIM(COALESCE({f}, '')) = '' 
               OR TRIM(COALESCE({f}, '')) NOT GLOB '[0-9]*.?[0-9]*'
        """)
    print("Fixed all non-numeric fee values to 0")