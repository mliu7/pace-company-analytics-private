from django.urls import path
from . import views

app_name = "reports"
urlpatterns = [
    path("", views.reports_list, name="reports_list"),
    path("new/", views.report_create, name="report_create"),
    path("groups/", views.report_groups, name="report_groups"),
    path("<uuid:pk>/", views.report_detail, name="report_detail"),
    path("<uuid:pk>/content/", views.report_content, name="report_content"),
    path("<uuid:pk>/audience/", views.report_audience, name="report_audience"),
]
