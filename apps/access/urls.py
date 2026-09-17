from django.urls import path

from . import views_auth, views_console, views_permissions

app_name = "access"
urlpatterns = [
    path("login/", views_auth.login_page, name="login"),
    path("denied/", views_auth.denied, name="denied"),
    path("logout/", views_auth.logout_view, name="logout"),
    path("view-as/", views_auth.view_as_start, name="view_as_start"),
    path("view-as/stop/", views_auth.view_as_stop, name="view_as_stop"),
    path("console/people/", views_console.people, name="console_people"),
    path("console/people/new/", views_console.create, name="console_create"),
    path("console/people/<int:pk>/", views_console.person, name="console_person"),
    path("console/audit/", views_console.audit_delegated, name="console_audit"),
    path("console/audit/all/", views_console.audit_all, name="console_audit_all"),
    path("console/usage/", views_console.usage, name="console_usage"),
    path("permissions/", views_permissions.permissions, name="permissions"),                    # the permission-design page (superadmin)
    path("permissions/update/", views_permissions.permissions_update, name="permissions_update"),   # POST: confirm / note / change a rule
]
