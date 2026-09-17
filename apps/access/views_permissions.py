"""The Permissions page (docs/07 "Permissions page"): one page where the superadmin sees every rule — capabilities, roles,
pages, in-page redactions, sign-in settings — with the current rule as the recommendation, confirms each with a check,
leaves notes, and changes what a page requires or what a role holds (audited overrides over the code defaults)."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import design, overrides, registry
from .audit import log
from .models import AccessDecision


def permissions(request):
    ctx = design.build()
    ctx.update({"nav": "permissions", "capabilities": registry.CAPABILITIES, "role_labels": {r: m["label"] for r, m in registry.ROLES.items()},
                "items_open": sum(1 for i in ctx["items"] if i["dec"]["status"] != "confirmed"),
                "roles_open": sum(1 for r in ctx["roles"] if r["dec"]["status"] != "confirmed"),
                "areas_open": sum(1 for a in ctx["areas"] if a["dec"]["status"] != "confirmed")})
    return render(request, "access/permissions.html", ctx)


def _back(request, anchor=""):
    nxt = request.POST.get("next") or "/access/permissions/"
    return redirect(nxt + (("#" + anchor) if anchor and "#" not in nxt else ""))


def _decide(key, status, account, note=None):
    kind, _, ident = key.partition(":")
    d, _created = AccessDecision.objects.get_or_create(key=key)
    if status is not None:
        d.status = status
        d.snapshot = design.snapshot_for(kind, ident) if status == "confirmed" else {}
        d.decided_by, d.decided_at = account, timezone.now()
    if note is not None:
        d.note = note.strip()
    d.save()
    return d


@require_POST
def permissions_update(request):
    """Actions: confirm · unconfirm · confirm_many (keys=) · note · set_view (caps[], conceal) · reset_view · set_role (caps[]) · reset_role."""
    acc = request.acc
    p = request.POST
    action, key = p.get("action", ""), p.get("key", "")
    anchor = p.get("anchor", "")
    try:
        if action in ("confirm", "unconfirm"):
            _decide(key, "confirmed" if action == "confirm" else "open", acc.real_account)
            log("write_action", request, meta_action="permissions." + action, meta_key=key)
        elif action == "confirm_many":
            keys = [k for k in p.getlist("keys") if k]
            for k in keys:
                _decide(k, "confirmed", acc.real_account)
            log("write_action", request, meta_action="permissions.confirm_many", meta_n=len(keys))
            messages.success(request, "Confirmed %d item%s." % (len(keys), "" if len(keys) == 1 else "s"))
        elif action == "note":
            _decide(key, None, acc.real_account, note=p.get("note", ""))
            log("write_action", request, meta_action="permissions.note", meta_key=key)
        elif action == "set_view":
            name = p.get("view", "")
            rule = overrides.set_view_rule(name, p.getlist("caps"), p.get("conceal") == "1", acc.real_account, p.get("reason", ""))
            _decide("view:" + name, "confirmed", acc.real_account)     # a rule Owner just set is a rule he agrees with
            log("write_action", request, meta_action="permissions.set_view", meta_key=name, meta_caps=list(rule["caps"]), meta_conceal=rule["conceal"])
            messages.success(request, "%s now requires %s." % (design.humanize(name), " + ".join(rule["caps"])))
        elif action == "reset_view":
            name = p.get("view", "")
            overrides.reset("view", name)
            _decide("view:" + name, "open", acc.real_account)
            log("write_action", request, meta_action="permissions.reset_view", meta_key=name)
            messages.success(request, "%s is back on the code default." % design.humanize(name))
        elif action == "set_role":
            role = p.get("role", "")
            caps = overrides.set_role_caps(role, p.getlist("caps"), acc.real_account, p.get("reason", ""))
            _decide("role:" + role, "confirmed", acc.real_account)
            log("write_action", request, meta_action="permissions.set_role", meta_key=role, meta_caps=sorted(caps))
            messages.success(request, "%s now holds %d capabilit%s." % (registry.ROLES[role]["label"], len(caps), "y" if len(caps) == 1 else "ies"))
        elif action == "reset_role":
            role = p.get("role", "")
            overrides.reset("role", role)
            _decide("role:" + role, "open", acc.real_account)
            log("write_action", request, meta_action="permissions.reset_role", meta_key=role)
            messages.success(request, "%s is back on the code default." % registry.ROLES.get(role, {}).get("label", role))
        else:
            messages.error(request, "Unknown action.")
    except ValueError as e:
        messages.error(request, str(e))
    return _back(request, anchor)
