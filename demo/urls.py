from django.urls import path

from . import views

urlpatterns = [
    path("try/", views.try_demo, name="demo-try"),
    path("try/leave/", views.leave, name="demo-leave"),
]
