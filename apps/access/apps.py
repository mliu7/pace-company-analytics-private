from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class AccessConfig(AppConfig):
    name = "apps.access"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Safety interlock (spec §3): the dev auto-login bypass may only exist on a DEBUG localhost box.
        if settings.PCA_AUTH_MODE == "dev" and not settings.DEBUG:
            raise ImproperlyConfigured("PCA_AUTH_MODE=dev requires DEBUG=True; refusing to start.")
        if settings.PCA_AUTH_MODE == "private" and not settings.PRIVATE_MODE:
            raise ImproperlyConfigured("Private authentication requires an explicit local settings profile")
        from . import registry
        registry.validate()
