"""Estimating workbench URLs (SharePoint spec §8). Names are the access-registry keys (apps/access/registry.py)."""

from django.urls import path

from . import views

app_name = "estimating"

urlpatterns = [
    path("", views.home, name="estimating_home"),
    path("search/", views.search, name="estimating_search"),
    path("item/<int:pk>/", views.item, name="estimating_item"),
    path("estimates/", views.estimates, name="estimating_estimates"),
    path("estimates/<int:pk>/", views.estimate_detail, name="estimate_detail"),
    path("estimates/save/", views.estimate_save, name="estimate_save"),
    path("estimates/<int:pk>/export/", views.estimate_export, name="estimate_export"),
    path("rates/", views.rates, name="estimating_rates"),
    path("rates/save/", views.rates_save, name="estimating_rates_save"),
    path("sources/", views.sources, name="estimating_sources"),
    path("import/", views.import_, name="estimating_import"),
]
