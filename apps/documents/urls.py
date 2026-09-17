"""Documents URLs (SharePoint spec §7). Names are the access-registry keys (apps/access/registry.py)."""

from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.documents_page, name="documents_page"),
    path("data/", views.documents_json, name="documents_json"),
    path("<int:pk>/", views.document_preview, name="document_preview"),
    path("<int:pk>/content/", views.document_content, name="document_content"),
    path("<int:pk>/html/", views.document_html, name="document_html"),
    path("<int:pk>/page/", views.document_page, name="document_page"),
    path("findings/", views.documents_findings, name="documents_findings"),
    path("link/", views.document_link_state, name="document_link_state"),
]
