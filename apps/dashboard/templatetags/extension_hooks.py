"""Optional entity content; inert unless an explicit profile installs a renderer."""
from django import template
from django.conf import settings
from django.utils.module_loading import import_string

register = template.Library()

@register.simple_tag(takes_context=True)
def extension_cards(context, kind, obj):
    renderer = getattr(settings, "PCA_ENTITY_CARDS_RENDERER", "")
    return import_string(renderer)(context, kind, obj) if renderer and obj else ""
