# accounts/decorators.py
from django.shortcuts import redirect
from functools import wraps

# TEACHER DECORATOR
def teacher_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == 'teacher':
            return view_func(request, *args, **kwargs)
        return redirect('login')  # redirect if not teacher
    return wrapper

# STUDENT DECORATOR
def student_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == 'student':
            return view_func(request, *args, **kwargs)
        return redirect('login')  # redirect if not student
    return wrapper

#ADMIN REQUIRED
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role in ['admin', 'proprietor', 'proprietress']:
            return view_func(request, *args, **kwargs)
        return redirect('login')  # redirect if not admin
    return wrapper