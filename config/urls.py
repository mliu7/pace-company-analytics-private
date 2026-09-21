from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("reports/", include("apps.reports.urls")),
    path("admin/", admin.site.urls),
    path("access/", include("apps.access.urls")),
    path("bids/", include("apps.bids.urls")),
    path("planning/schedule/", include("apps.scheduling.urls")),
    path("planning/planner/", include("apps.planner.urls")),
    path("planning/", include("apps.planning.urls")),
    path("documents/", include("apps.documents.urls")),
    path("estimating/", include("apps.estimating.urls")),
    path("", include("apps.dashboard.urls")),
]

if settings.PCA_AUTH_MODE == "sso":
    urlpatterns.append(path("oidc/", include(("mozilla_django_oidc.urls", "oidc"), namespace="oidc")))

for extension in getattr(settings, "PCA_EXTRA_URLCONFS", ()):
    urlpatterns.insert(0, path("", include(extension)))
