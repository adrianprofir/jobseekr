from django.urls import path

from . import views

urlpatterns = [
    path("", views.application_list, name="application-list"),
    path("applications/new/", views.application_create, name="application-create"),
    path("applications/<int:pk>/", views.application_detail, name="application-detail"),
    path("applications/<int:pk>/edit/", views.application_edit, name="application-edit"),
    path("applications/<int:pk>/status/", views.application_status, name="application-status"),
    path("applications/<int:pk>/notes/", views.application_note, name="application-note"),
    path("applications/<int:pk>/delete/", views.application_delete, name="application-delete"),
    path("applications/<int:pk>/follow-up/done/", views.follow_up_done, name="follow-up-done"),
    path(
        "applications/<int:pk>/follow-up/snooze/", views.follow_up_snooze, name="follow-up-snooze"
    ),
    path("follow-ups/", views.follow_up_list, name="follow-up-list"),
    path("summary/", views.weekly_summary, name="weekly-summary"),
    path("companies/", views.company_list, name="company-list"),
    path("companies/<int:pk>/", views.company_detail, name="company-detail"),
    path("companies/<int:pk>/edit/", views.company_edit, name="company-edit"),
    path("companies/<int:pk>/delete/", views.company_delete, name="company-delete"),
    path("documents/", views.document_list, name="document-list"),
    path("documents/upload/", views.document_upload, name="document-upload"),
    path("documents/<int:pk>/file/", views.document_download, name="document-download"),
    path("documents/<int:pk>/delete/", views.document_delete, name="document-delete"),
    path("healthz", views.healthz, name="healthz"),
]
