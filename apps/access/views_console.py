"""Access console (Access Spec v1 §8): people & role management for the permission admin,
plus superadmin-only extra grants, full audit and usage dashboards. Concealment rules per §9."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import Employee

from . import registry
from .audit import log
from .models import Account, AuditEvent, ExtraGrant, RoleAssignment


def _ctx(request, nav_sub, **extra):
    # visible_roles = roles the console may name/tag; assignable_roles = the ones this actor may hand out
    # (Permission Admin is an ordinary role, but only a superadmin grants it — §17.5, registry.SUPERADMIN_ASSIGNED).
    sup = bool(getattr(request, "acc", None) and request.acc.is_superadmin)
    ctx = {"nav": "console", "nav_sub": nav_sub, "visible_roles": registry.visible_roles(),
           "assignable_roles": registry.assignable_roles(sup), "superadmin_assigned": registry.SUPERADMIN_ASSIGNED}
    ctx.update(extra)
    return ctx


def people(request):
    accounts = Account.objects.select_related("employee").prefetch_related("role_assignments").order_by("display_name")
    return render(request, "access/console_people.html", _ctx(request, "people", accounts=accounts))


def person(request, pk):
    acct = get_object_or_404(Account.objects.select_related("employee"), pk=pk)
    acc = request.acc
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("grant_role", "revoke_role") and acc.can("roles.assign"):
            role = request.POST.get("role")
            meta = registry.ROLES.get(role)
            # permission_admin itself is superadmin-assignable only (§17.5) — an admin cannot mint peers
            if meta is None or role not in registry.assignable_role_slugs(acc.is_superadmin):
                raise Http404
            if action == "grant_role":
                divisions = [d.strip() for d in (request.POST.get("divisions") or "").split(",") if d.strip()] or None
                if meta["scoped"] and not divisions:
                    messages.error(request, "This role requires division codes (e.g. 070).")
                    return redirect("access:console_person", pk=pk)
                RoleAssignment.objects.update_or_create(account=acct, role=role,
                                                        defaults={"division_codes": divisions, "granted_by": acc.real_account})
                log("role_granted", request, target=acct, capability_or_role=role, divisions=divisions)
                messages.success(request, "Granted %s." % meta["label"])
            else:
                RoleAssignment.objects.filter(account=acct, role=role).delete()
                log("role_revoked", request, target=acct, capability_or_role=role)
                messages.success(request, "Revoked %s." % meta["label"])
        elif action in ("disable", "enable") and acc.can("accounts.manage"):
            if acct.is_superadmin and not acc.is_superadmin:
                raise Http404
            acct.status = "disabled" if action == "disable" else "active"
            acct.save(update_fields=["status"])
            if action == "disable" and acct.user:      # kill sessions
                from django.contrib.sessions.models import Session
                for sess in Session.objects.all():
                    if str(sess.get_decoded().get("_auth_user_id")) == str(acct.user_id):
                        sess.delete()
            log("account_disabled" if action == "disable" else "account_enabled", request, target=acct)
        elif action == "link_employee" and acc.can("accounts.manage"):
            emp_id = request.POST.get("employee_id") or None
            acct.employee = Employee.objects.filter(pk=emp_id).first() if emp_id else None
            acct.save(update_fields=["employee"])
            messages.success(request, "Employee link updated.")
        # ---- superadmin-only actions (concealed capabilities) ----
        elif action in ("grant_extra", "revoke_extra") and acc.can("grants.manage"):
            cap = request.POST.get("capability")
            if cap not in registry.CAPABILITIES:
                raise Http404
            if action == "grant_extra":
                ExtraGrant.objects.get_or_create(account=acct, capability=cap, defaults={"granted_by": acc.real_account})
                log("extra_granted", request, target=acct, capability_or_role=cap)
            else:
                ExtraGrant.objects.filter(account=acct, capability=cap).delete()
                log("extra_revoked", request, target=acct, capability_or_role=cap)
        elif action == "toggle_superadmin" and acc.can("grants.manage"):
            acct.is_superadmin = not acct.is_superadmin
            acct.save(update_fields=["is_superadmin"])
            if acct.user:
                acct.user.is_staff = acct.user.is_superuser = acct.is_superadmin
                acct.user.save(update_fields=["is_staff", "is_superuser"])
            log("superadmin_changed", request, target=acct, granted=acct.is_superadmin)
        else:
            raise Http404
        return redirect("access:console_person", pk=pk)

    assigned = {ra.role: ra for ra in acct.role_assignments.all()}
    extras = list(acct.extra_grants.all()) if acc.is_superadmin else []
    all_caps = registry.CAPABILITIES if acc.is_superadmin else {}
    employees = Employee.objects.filter(active=True).order_by("canonical_name")[:2000]
    return render(request, "access/console_person.html", _ctx(request, "people", a=acct, assigned=assigned, extras=extras,
                                                              all_caps=all_caps, employees=employees))


def create(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        name = (request.POST.get("display_name") or "").strip()
        if not email or "@" not in email or not name:
            messages.error(request, "Email and name are required.")
            return redirect("access:console_create")
        if Account.objects.filter(email=email).exists():
            messages.error(request, "An account for %s already exists." % email)
            return redirect("access:console_create")
        user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
        user.set_unusable_password(); user.save()
        emp = Employee.objects.filter(pk=request.POST.get("employee_id") or None).first()
        acct = Account.objects.create(email=email, display_name=name, user=user, employee=emp, created_by=request.acc.real_account)
        log("account_created", request, target=acct)
        messages.success(request, "Account created — assign roles below. They can sign in with Microsoft whenever ready.")
        return redirect("access:console_person", pk=acct.pk)
    employees = Employee.objects.filter(active=True).order_by("canonical_name")[:2000]
    return render(request, "access/console_create.html", _ctx(request, "people", employees=employees))


def audit_delegated(request):
    """Permission-change log for the permission admin — superadmin-tier events excluded (§9.1)."""
    hidden_caps = registry.superadmin_caps()
    events = [e for e in AuditEvent.objects.select_related("actor", "target")
              .filter(kind__in=["account_created", "account_disabled", "account_enabled", "role_granted", "role_revoked", "login", "login_denied"])
              .order_by("-at")[:400] if e.capability_or_role not in hidden_caps]
    return render(request, "access/console_audit.html", _ctx(request, "audit", events=events, full=False))


def audit_all(request):
    events = AuditEvent.objects.select_related("actor", "target", "acting_as").exclude(kind="page_view").order_by("-at")[:500]
    return render(request, "access/console_audit.html", _ctx(request, "audit", events=events, full=True))


def usage(request):
    since = timezone.now() - timedelta(days=30)
    per_user = (AuditEvent.objects.filter(kind="page_view", at__gte=since).values("actor__display_name")
                .annotate(views=Count("id"), last=Max("at")).order_by("-views"))
    per_page = (AuditEvent.objects.filter(kind="page_view", at__gte=since).values("view_name")
                .annotate(views=Count("id"), users=Count("actor", distinct=True)).order_by("-views"))
    denied = (AuditEvent.objects.filter(kind="denied", at__gte=since).values("actor__display_name", "path")
              .annotate(n=Count("id")).order_by("-n")[:30])
    return render(request, "access/console_usage.html", _ctx(request, "usage", per_user=per_user, per_page=per_page, denied=denied))
