"""Template tags behind the document partials on the project / customer / bid pages (SharePoint spec §7.2). The
partials are plain `{% include %}`s with the page's own object in context, so the tags fetch the files themselves.
Every tag returns an empty dict on any error (missing tables before the migration, a broken FK …) — a page must
never fail because of its documents card."""

import logging
from collections import defaultdict
from datetime import timedelta

from django import template
from django.urls import reverse
from django.utils import timezone

from ..models import LOW_CONFIDENCE, File, ListAttachment

register = template.Library()
log = logging.getLogger(__name__)
RECENT_DAYS = 14


def _group(files):
    """Files grouped by location (repo) then folder, in a stable order; each file carries its display bits."""
    now = timezone.now()
    repos = {}
    low = recent = 0
    for f in files:
        f.kind = _kind(f.ext)
        f.is_low = f.link_confidence is not None and float(f.link_confidence) < LOW_CONFIDENCE
        f.is_recent = bool(f.mtime and f.mtime >= now - timedelta(days=RECENT_DAYS))
        f.preview_url = reverse("documents:document_preview", args=[f.id])
        low += f.is_low
        recent += f.is_recent
        r = repos.setdefault(f.repo_id, {"repo": f.repo, "is_share": f.repo.is_share, "folders": defaultdict(list), "n": 0})
        r["folders"][f.folder_path].append(f)
        r["n"] += 1
    out = []
    for r in sorted(repos.values(), key=lambda r: (r["is_share"], r["repo"].name)):
        # folder labels relative to the longest common folder prefix (one job folder for a bid / project card), so the
        # rows read '04. Pace Proposal' instead of the whole P: path every time; the prefix is shown once on the group
        paths = sorted(r["folders"])
        prefix = ""
        if len(paths) > 1:
            parts = [p.split("/") for p in paths]
            common = []
            for segs in zip(*parts):
                if all(x == segs[0] for x in segs):
                    common.append(segs[0])
                else:
                    break
            prefix = "/".join(common)
        elif paths and paths[0]:
            prefix = paths[0].rsplit("/", 1)[0] if "/" in paths[0] else ""
        r["prefix"] = prefix
        r["folders"] = [{"path": k, "label": (k[len(prefix):].lstrip("/") if prefix and k.startswith(prefix) else k) or "(job folder)",
                         "files": sorted(v, key=lambda f: (f.mtime or now), reverse=True)} for k, v in sorted(r["folders"].items())]
        out.append(r)
    return out, low, recent


def _kind(ext):
    from ..views import kind_of
    return kind_of(ext)


def _build(qs, extra_url, **more):
    files = list(qs.select_related("repo", "linked_project", "linked_bid").order_by("-mtime"))
    if not files and not more.get("attachments"):
        return {}
    groups, low, recent = _group(files)
    recent_files = [f for f in files if f.is_recent][:12]
    findings = sum(1 for f in files if getattr(f, "n_findings", 0))
    return {"n": len(files), "groups": groups, "low": low, "recent": recent_files, "n_recent": recent, "page_url": extra_url, "low_threshold": LOW_CONFIDENCE, **more}


@register.simple_tag
def project_documents(p):
    """{'n', 'groups', 'low', 'recent', 'page_url'} for a project page, or {} when the job has no documents."""
    try:
        return _build(File.objects.filter(is_deleted=False, linked_project=p), reverse("documents:documents_page") + "?project=" + p.canonical_project_number)
    except Exception:  # noqa
        log.exception("project_documents failed for %s", getattr(p, "pk", "?"))
        return {}


@register.simple_tag
def customer_documents(c):
    try:
        qs = File.objects.filter(is_deleted=False).filter(models_q_customer(c))
        return _build(qs, reverse("documents:documents_page") + "?customer=" + c.sl_customer_id)
    except Exception:  # noqa
        log.exception("customer_documents failed for %s", getattr(c, "pk", "?"))
        return {}


def models_q_customer(c):
    from django.db.models import Q
    return Q(linked_customer=c) | Q(linked_project__customer=c) | Q(linked_bid__client=c)


@register.simple_tag
def bid_documents(bid):
    """Files linked to the bid itself or to the SL job it became, plus the Project List attachment placeholders."""
    try:
        from django.db.models import Q
        q = Q(linked_bid=bid)
        if getattr(bid, "project_id", None):
            q |= Q(linked_project_id=bid.project_id)
        atts = list(ListAttachment.objects.filter(bid=bid))
        # the bid's own job folder(s) on the P: drive / SharePoint, shown even before they hold a file
        from ..models import DocLink
        from apps.ingestion.sources import share_client
        folders = []
        for l in DocLink.objects.filter(bid=bid, folder__isnull=False).exclude(state=DocLink.State.REJECTED).select_related("folder", "folder__repo").order_by("-confidence")[:6]:
            fo = l.folder
            if fo.is_deleted:
                continue
            folders.append({"path": fo.path, "repo": fo.repo, "is_share": fo.repo.is_share, "rule": l.rule, "confidence": float(l.confidence),
                            "unc": share_client.unc_path(fo.path) if fo.repo.is_share else "", "open_url": share_client.smb_url(fo.path) if fo.repo.is_share else fo.web_url,
                            "n_files": File.objects.filter(folder=fo, is_deleted=False).count() + File.objects.filter(is_deleted=False, path__startswith=fo.path + "/", repo=fo.repo).count()})
        out = _build(File.objects.filter(is_deleted=False).filter(q), reverse("documents:documents_page") + "?bid=%d" % bid.id, attachments=atts, bid=bid)
        if folders:
            out = dict(out or {"n": 0, "groups": [], "low": 0, "recent": [], "n_recent": 0, "page_url": reverse("documents:documents_page") + "?bid=%d" % bid.id, "low_threshold": LOW_CONFIDENCE, "attachments": atts, "bid": bid}, folders=folders)
        return out
    except Exception:  # noqa
        log.exception("bid_documents failed for %s", getattr(bid, "pk", "?"))
        return {}


@register.filter
def filesize(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit) if n < 10 else "%.0f %s" % (n, unit))
        n /= 1024


@register.filter
def conf_label(conf):
    from ..linking import confidence_label
    return confidence_label(conf)


@register.filter
def docjson(value):
    """JSON for an inline <script> (dicts / lists of plain values), '<' escaped so it cannot close the tag."""
    import json
    from django.utils.safestring import mark_safe
    return mark_safe(json.dumps(value, default=str).replace("<", "\\u003c"))
