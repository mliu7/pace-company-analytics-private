"""Login / logout / denied / user-switcher endpoints (Access Spec v1 §3, §10)."""

from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme

from .audit import log
from .context import SESSION_VIEW_AS
from .models import Account


def login_page(request):
    if request.user.is_authenticated:
        return redirect("/")
    next_url = request.GET.get("next", "/")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()):
        next_url = "/"
    return render(request, "access/login.html", {"sso": settings.PCA_AUTH_MODE == "sso", "next_url": next_url})


def denied(request):
    return render(request, "access/denied.html", {"reason": request.GET.get("why", "unknown")}, status=403)


def logout_view(request):
    log("logout", request)
    request.session.pop(SESSION_VIEW_AS, None)
    auth_logout(request)
    return redirect(settings.LOGIN_URL)


@require_POST
def view_as_start(request):
    """Superadmin-only (enforced by URL_ACCESS): render the app exactly as the target account sees it."""
    target = Account.objects.filter(pk=request.POST.get("account_id"), status="active").first()
    if target is None:
        raise Http404
    request.session[SESSION_VIEW_AS] = target.pk
    log("impersonation_start", request, target=target)
    return redirect(request.POST.get("next") or "/")


@require_POST
def view_as_stop(request):
    target_id = request.session.pop(SESSION_VIEW_AS, None)
    if target_id:
        log("impersonation_stop", request, target=Account.objects.filter(pk=target_id).first())
    return redirect(request.POST.get("next") or "/")
