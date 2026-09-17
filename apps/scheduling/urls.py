"""Resource Scheduler URLs (SharePoint spec §6.3) — mounted at /planning/schedule/ (config/urls.py). The four names are
registered in apps/access/registry.py (planning.view; the edit endpoint planning.write)."""

from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.schedule, name="planning_schedule"),
    path("data/", views.schedule_json, name="planning_schedule_json"),
    path("edit/", views.schedule_edit, name="planning_schedule_edit"),
    path("export/", views.schedule_export, name="planning_schedule_export"),
]
