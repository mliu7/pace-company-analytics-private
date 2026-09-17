"""Production planning URLs (SharePoint spec §6, §10). Names are the access-registry keys."""

from django.urls import path

from . import views, views_approvals, views_punch, views_tasks

app_name = "planning"

urlpatterns = [
    path("", views.today, name="planning_today"),
    path("status/", views.status_board, name="planning_status"),
    path("status/data/", views.status_json, name="planning_status_json"),
    path("status/edit/", views.status_edit, name="planning_status_edit"),
    path("status/import/", views.status_import, name="planning_status_import"),
    path("status/send/", views.status_send, name="planning_status_send"),
    path("tasks/", views_tasks.tasks, name="planning_tasks"),
    path("tasks/report/", views_tasks.tasks_report, name="planning_tasks_report"),
    path("tasks/export/", views_tasks.tasks_export, name="planning_tasks_export"),
    path("punch/", views_punch.punch, name="planning_punch"),
    path("punch/<int:pk>/", views_punch.punch_project, name="planning_punch_project"),
    path("punch/edit/", views_punch.punch_edit, name="planning_punch_edit"),
    path("punch/export/", views_punch.punch_export, name="planning_punch_export"),
    path("approvals/", views_approvals.approvals, name="planning_approvals"),
    path("approvals/edit/", views_approvals.approval_edit, name="planning_approval_edit"),
    path("approvals/decide/", views_approvals.approval_decide, name="planning_approval_decide"),
    path("approvals/export/", views_approvals.approval_export, name="planning_approval_export"),
    path("approvals/attachment/<int:pk>/", views_approvals.approval_attachment, name="planning_approval_attachment"),
]
