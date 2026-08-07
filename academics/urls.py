from django.urls import path
from . import views

urlpatterns = [
    # example route (test route)
    path('academics-test/', views.academics_test, name='academics-test'),
]