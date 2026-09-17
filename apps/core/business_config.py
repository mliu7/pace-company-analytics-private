from django.conf import settings
from django.core.cache import cache


def get(key, default=None):
    overrides = getattr(settings, "PCA_BUSINESS_SETTINGS", None)
    if overrides is not None:
        return overrides.get(key, default)
    cache_key = "business-config:" + key
    value = cache.get(cache_key)
    if value is None:
        from .models import BusinessConfiguration

        row = (
            BusinessConfiguration.objects.filter(pk=key)
            .values_list("value", flat=True)
            .first()
        )
        value = {"value": row if row is not None else default}
        cache.set(cache_key, value, 30)
    return value["value"]
