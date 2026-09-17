"""Default-deny access middleware (Access Spec v1 §12).

Order of operations per request:
  1. static → pass through
  2. authenticate (dev auto-login only on DEBUG+localhost; else OIDC session)
  3. resolve URL → registry.URL_ACCESS (namespace rules for admin/oidc); unlisted → concealed deny
  4. build AccessContext (resolves impersonation), attach request.acc
  5. capability check — conceal ⇒ 404, else 403 (both audited)
  6. impersonation write-block (only "view_as_stop" may POST while impersonating)
  7. page_view audit on successful HTML GETs
"""

from django.conf import settings
from django.contrib.auth import login as auth_login
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from . import context as ctx_mod
from . import registry
from .audit import log

DEV_HOSTS = {"127.0.0.1", "localhost", "testserver"}
POLL_VIEWS = {"refresh_status"}          # not worth page_view rows


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.PRIVATE_MODE:
            from apps.private_workspace.middleware import serve
            return serve(request, self.get_response)
        path = request.path
        if path.startswith(("/insights/", "/ratings/", "/ask/", "/private/")):
            from django.http import HttpResponseNotFound
            return HttpResponseNotFound()
        if path.startswith(settings.STATIC_URL) or path == "/favicon.ico":
            return self.get_response(request)

        ns = request.resolver_match.namespaces if request.resolver_match else None
        # resolver_match is not set yet in process_request; resolve lazily:
        from django.urls import resolve
        try:
            match = resolve(path)
        except Http404:
            match = None

        # ---- namespace shortcuts
        if match is not None and "oidc" in (match.namespaces or []):
            return self.get_response(request)

        rule = None
        if match is not None:
            if "admin" in (match.namespaces or []):
                rule = {"caps": ("ops.view",), "conceal": True}
            else:
                rule = registry.rule_for(match.url_name)       # code default, adjusted by the Permissions page's overrides

        # ---- authentication
        account = None
        if request.user.is_authenticated:
            account = self._account_for(request.user)
        elif settings.PCA_AUTH_MODE == "dev" and request.get_host().split(":")[0] in DEV_HOSTS and settings.DEBUG:
            account = self._dev_login(request)

        if rule and rule.get("public"):
            request.acc = ctx_mod.build(request, account) if account else ctx_mod.anonymous_context()
            return self.get_response(request)

        if account is None or account.status != "active":
            if request.user.is_authenticated:      # authenticated but unknown/disabled → denied page
                log("login_denied", request, actor=account, meta_email=getattr(request.user, "email", ""))
                return render(request, "access/denied.html", {"reason": "disabled" if account else "unknown"}, status=403)
            return redirect(settings.LOGIN_URL + ("?next=" + request.get_full_path() if request.method == "GET" else ""))

        request.acc = acc = ctx_mod.build(request, account)

        # ---- authorization
        if rule is None:
            log("denied", request, meta_reason="unregistered_url")
            raise Http404
        if not rule.get("auth"):
            missing = [c for c in rule["caps"] if not acc.can(c)]
            if missing:
                log("denied", request, capability_or_role=missing[0])
                # Smart landing comes BEFORE concealment: "/" is the Command Center, which is superadmin-only
                # (2026-09-12), and nobody should meet a 404 at the front door — they are sent to their first section
                # instead, which reveals nothing about what lives at "/".
                if match.url_name == "command_center" and request.path == "/" and not request.GET:
                    for cap, dest in (("projects.view", "/projects/"), ("finance.view", "/finance/daily/"),
                                      ("sales010.view", "/sales/010/"), ("bids.view", "/bids/"),
                                      ("planning.view", "/planning/"), ("console.view", "/access/console/people/")):
                        if acc.can(cap):
                            return redirect(dest)
                    return redirect("/about/")
                if rule.get("conceal"):
                    raise Http404
                return render(request, "access/denied.html", {"reason": "forbidden"}, status=403)

        # ---- impersonation write-block (§10)
        if acc.viewing_as is not None and request.method not in ("GET", "HEAD", "OPTIONS") and match.url_name != "view_as_stop":
            log("denied", request, meta_reason="write_blocked_while_impersonating")
            return JsonResponse({"error": "writes are blocked while viewing as another user"}, status=403)

        response = self.get_response(request)

        if (request.method == "GET" and response.status_code == 200 and match.url_name not in POLL_VIEWS
                and response.get("Content-Type", "").startswith("text/html")):
            log("page_view", request, division=request.GET.get("div", ""))
        return response

    # ------------------------------------------------------------------
    def _account_for(self, user):
        from .models import Account
        return Account.objects.select_related("employee").filter(user=user).first()

    def _dev_login(self, request):
        from django.contrib.auth.models import User
        from .models import Account
        acct = Account.objects.filter(email="dev@pca.local").first()
        if acct is None:
            user = User.objects.create_user("dev@pca.local", email="dev@pca.local")
            user.set_unusable_password(); user.save()
            user.is_staff = user.is_superuser = True; user.save()
            acct = Account.objects.create(email="dev@pca.local", display_name="Owner (dev)", user=user, is_superadmin=True)
        auth_login(request, acct.user, backend="django.contrib.auth.backends.ModelBackend")
        acct.last_login_at = timezone.now()
        acct.save(update_fields=["last_login_at"])
        return acct
