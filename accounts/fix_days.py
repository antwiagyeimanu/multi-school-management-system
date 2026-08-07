from accounts.models import Term
from accounts.views import calculate_school_days

terms = Term.objects.all()

for term in terms:
    old = term.days_opened
    new = calculate_school_days(term.start_date, term.end_date, term.school)
    
    if old != new:
        print(f"{term} : {old} -> {new}")
        term.days_opened = new
        term.save()

print("All done.")