"""Exercise the production OIDC namespace in the isolated shared test profile."""
from django.urls import include, path
from config.urls import urlpatterns as app_patterns

urlpatterns = app_patterns + [path("oidc/", include(("mozilla_django_oidc.urls", "oidc"), namespace="oidc"))]
