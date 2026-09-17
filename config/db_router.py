"""Private records never migrate into, or fall back to, the company database.

Ratings retain their historical model labels so existing computations and saved
IDs continue to work. All their reads/writes route together with the private apps.
No relationship may span the two stores; entity references are stable strings.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PRIVATE_APPS = {"notes", "private_workspace"}
PRIVATE_MODELS = {("analytics", "ratingrun"), ("analytics", "entityrating")}


def is_private(app, model=None):
    return app in PRIVATE_APPS or (app, (model or "").lower()) in PRIVATE_MODELS


class WorkspaceRouter:
    def _alias(self, model):
        if is_private(model._meta.app_label, model._meta.model_name):
            if not settings.PRIVATE_MODE:
                raise ImproperlyConfigured(
                    "Private records are unavailable in the shared application"
                )
            return "private"
        return "default"

    db_for_read = lambda self, model, **hints: self._alias(model)
    db_for_write = lambda self, model, **hints: self._alias(model)

    def allow_relation(self, obj1, obj2, **hints):
        return is_private(obj1._meta.app_label, obj1._meta.model_name) == is_private(
            obj2._meta.app_label, obj2._meta.model_name
        )

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        private = is_private(app_label, model_name)
        if db == "private":
            return private
        return not private
