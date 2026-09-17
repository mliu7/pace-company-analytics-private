"""Placeholder views for pages whose build phase has not landed yet — every URL name exists from day one so the
access registry's default-deny contract (tests/access/test_registry.py) stays whole while phases ship."""

from django.shortcuts import render

from .views import _ctx


def pending(nav, title, phase, subtitle=""):
    def view(request, *args, **kwargs):
        return render(request, "dashboard/phase_pending.html", _ctx(request, nav, title=title, phase=phase, subtitle=subtitle))
    view.__name__ = "pending_%s" % nav
    return view
