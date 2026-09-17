"""Planner boards URLs (SharePoint spec §9) — mounted at /planning/planner/ (config/urls.py). Names are the access-registry keys."""

from django.urls import path

from . import views

app_name = "planner"

urlpatterns = [
    path("", views.planner_page, name="planning_planner"),
    path("data/", views.planner_json, name="planning_planner_json"),
]
