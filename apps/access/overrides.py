"""The Permissions page's override layer (docs/07 "Permissions page").

registry.py holds the code defaults — Owner's recommendations, and the reset point. A superadmin can change what a page
requires or what a role holds on /access/permissions/; the change is stored in access_accessoverride and applied here,
on every request, over the default. Guardrails (Access Spec v1 §9): a role can never receive a superadmin-tier
capability; a page that requires a superadmin-tier capability is always concealed (404); public / signed-in-only
pages (login, denied, logout, about, search) are not overridable.
"""

from django.db import DatabaseError

from . import registry

FIXED_VIEWS = {n for n, r in registry.URL_ACCESS.items() if r.get("public") or r.get("auth")}


def _all():
    """{(kind, key): value} — every override, fresh from the small table (an empty dict before the table exists)."""
    try:
        from .models import AccessOverride
        return {(o.kind, o.key): o.value for o in AccessOverride.objects.all()}
    except DatabaseError:
        return {}


def view_rule(url_name, base):
    v = _all().get(("view", url_name))
    if not v or url_name in FIXED_VIEWS:
        return base
    caps = tuple(c for c in v.get("caps", []) if c in registry.CAPABILITIES)
    conceal = bool(v.get("conceal")) or bool(set(caps) & registry.superadmin_caps())
    return {"caps": caps, "conceal": conceal}


def role_caps(role, base):
    v = _all().get(("role", role))
    if not v:
        return set(base)
    return {c for c in v.get("caps", []) if c in registry.CAPABILITIES and c not in registry.superadmin_caps()}


def set_view_rule(url_name, caps, conceal, account, reason=""):
    """Store a page rule; returns the effective rule. Raises ValueError on an invalid request."""
    if url_name not in registry.URL_ACCESS or url_name in FIXED_VIEWS:
        raise ValueError("this page's rule is fixed")
    caps = [c for c in dict.fromkeys(caps) if c in registry.CAPABILITIES]
    if not caps:
        raise ValueError("a page must require at least one capability")
    conceal = bool(conceal) or bool(set(caps) & registry.superadmin_caps())
    from .models import AccessOverride
    AccessOverride.objects.update_or_create(kind="view", key=url_name, defaults={"value": {"caps": caps, "conceal": conceal}, "set_by": account, "reason": reason})
    return {"caps": tuple(caps), "conceal": conceal}


def set_role_caps(role, caps, account, reason=""):
    if role not in registry.ROLES:
        raise ValueError("unknown role")
    bad = set(caps) & registry.superadmin_caps()
    if bad:
        raise ValueError("a role can never hold a superadmin-tier capability (%s)" % ", ".join(sorted(bad)))
    caps = [c for c in dict.fromkeys(caps) if c in registry.CAPABILITIES]
    from .models import AccessOverride
    AccessOverride.objects.update_or_create(kind="role", key=role, defaults={"value": {"caps": caps}, "set_by": account, "reason": reason})
    return set(caps)


def reset(kind, key):
    from .models import AccessOverride
    return AccessOverride.objects.filter(kind=kind, key=key).delete()[0]


def changed_keys():
    """{(kind, key)} of rules that differ from the code default right now."""
    out = set()
    for (kind, key), v in _all().items():
        if kind == "view" and key in registry.URL_ACCESS:
            base = registry.URL_ACCESS[key]
            eff = view_rule(key, base)
            if set(eff.get("caps", ())) != set(base.get("caps", ())) or bool(eff.get("conceal")) != bool(base.get("conceal")):
                out.add((kind, key))
        elif kind == "role" and key in registry.ROLES:
            if role_caps(key, set(registry.ROLES[key]["caps"])) != set(registry.ROLES[key]["caps"]):
                out.add((kind, key))
    return out
