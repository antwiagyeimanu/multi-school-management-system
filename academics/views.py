from django.shortcuts import render
from django.http import HttpResponse

def academics_test(request):
 return render(request, "academics/dashboard.html")