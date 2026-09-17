"""Audit helpers (Access Spec v1 §11)."""

from .models import AuditEvent


def _ip(request):
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")) or None


def log(kind, request=None, actor=None, acting_as=None, target=None, capability_or_role="", view_name="", path="", division="", **meta):
    from django.conf import settings
    if settings.PRIVATE_MODE:
        from apps.private_workspace.models import PrivateAudit
        return PrivateAudit.objects.create(kind=kind, detail={"path": path or (request.path if request else ""), **meta})
    acc = getattr(request, "acc", None) if request else None
    if actor is None and acc is not None:
        actor = acc.real_account
    if acting_as is None and acc is not None:
        acting_as = acc.viewing_as
    AuditEvent.objects.create(
        kind=kind, actor=actor, acting_as=acting_as, target=target, capability_or_role=capability_or_role,
        view_name=view_name or (request.resolver_match.url_name if request is not None and request.resolver_match else ""),
        path=path or (request.get_full_path()[:300] if request is not None else ""),
        division=division, ip=_ip(request), meta=meta or {})
