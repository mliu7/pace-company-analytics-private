"""Documents pages (SharePoint spec §7.2): the browse page + its JSON, the preview / content stream (cached under
APP_SUPPORT_DIR/doc_cache, behind documents.view), the findings queue and the confirm / reject endpoint for links.
Every URL name here is registered in apps/access/registry.py (default-deny); the middleware enforces the capability
before the view runs, and the content stream checks it again itself."""

import hashlib
import mimetypes
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.access.audit import log as audit_log
from apps.core.models import Customer, Project
from apps.dashboard.views import _ctx
from apps.ingestion.bulk import fetch_dict

from . import checks, extract, linking, loaders, preview as previewer
from .models import LOW_CONFIDENCE, DocFinding, DocLink, DocText, File, Folder, Repo

KINDS = [("pdf", "PDF"), ("sheet", "Spreadsheet"), ("doc", "Document"), ("image", "Image"), ("email", "Email"), ("drawing", "Drawing"),
         ("note", "Notes"), ("media", "Media"), ("archive", "Archive"), ("other", "Other")]
_KIND = {"pdf": "pdf", "xlsx": "sheet", "xlsm": "sheet", "xls": "sheet", "csv": "sheet", "docx": "doc", "doc": "doc", "rtf": "doc", "txt": "doc", "md": "doc",
         "pptx": "doc", "ppt": "doc", "jpg": "image", "jpeg": "image", "png": "image", "gif": "image", "heic": "image", "webp": "image", "bmp": "image",
         "tif": "image", "tiff": "image", "svg": "image", "msg": "email", "eml": "email", "dwg": "drawing", "dxf": "drawing", "vsdx": "drawing", "vsd": "drawing",
         "pdfx": "drawing", "one": "note", "onetoc2": "note", "loop": "note", "mp4": "media", "mov": "media", "mp3": "media", "m4a": "media", "wav": "media",
         "zip": "archive", "7z": "archive", "rar": "archive"}
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
CONTENT_CAP = 50 * 1024 * 1024


def kind_of(ext):
    return _KIND.get((ext or "").lower(), "other")


def _link_state(conf, has_target):
    if not has_target:
        return "unlinked"
    return "check" if float(conf or 0) < LOW_CONFIDENCE else "linked"


# ------------------------------------------------------------------------------------------- browse page
def _repo_stats():
    rows = fetch_dict("""
        SELECT r.id, r.key, r.name, r.kind, r.division, r.last_indexed, r.last_error, r.web_url, r.root_path,
               COUNT(f.id) files, COUNT(f.id) FILTER (WHERE f.linked_project_id IS NOT NULL OR f.linked_bid_id IS NOT NULL OR f.linked_customer_id IS NOT NULL) linked,
               COUNT(f.id) FILTER (WHERE (f.linked_project_id IS NOT NULL OR f.linked_bid_id IS NOT NULL) AND f.link_confidence < %s) low,
               COUNT(f.id) FILTER (WHERE f.mtime >= now() - interval '14 days') changed,
               COUNT(f.id) FILTER (WHERE f.text_status = 'ok') text_ok, COUNT(f.id) FILTER (WHERE f.text_status = 'failed') text_failed,
               (SELECT COUNT(*) FROM documents_folder x WHERE x.repo_id = r.id AND NOT x.is_deleted) folders
        FROM documents_repo r LEFT JOIN documents_file f ON f.repo_id = r.id AND NOT f.is_deleted
        GROUP BY r.id ORDER BY r.kind DESC, r.name""", [LOW_CONFIDENCE])
    for r in rows:
        r["unlinked"] = r["files"] - r["linked"]
        r["is_share"] = r["kind"] == "share"
        r["linked_pct"] = (r["linked"] / r["files"]) if r["files"] else None
    return rows


def _hygiene():
    """Folder-naming conventions per library / division: which style each project-level folder follows (depth ≤ 2,
    the level where job folders live), with the folders behind every count."""
    out = []
    folders = Folder.objects.filter(is_deleted=False, depth__lte=2).select_related("repo").order_by("repo__kind", "repo__name", "path")
    by_repo = defaultdict(lambda: {"styles": Counter(), "examples": defaultdict(list), "n": 0})
    for fo in folders:
        st = linking.folder_style(fo.name)
        b = by_repo[fo.repo]
        b["styles"][st] += 1
        b["n"] += 1
        if len(b["examples"][st]) < 12:
            b["examples"][st].append(fo)
    for repo, b in by_repo.items():
        numbered = sum(v for k, v in b["styles"].items() if k != "name_only")
        out.append({"repo": repo, "n": b["n"], "numbered": numbered, "pct": (numbered / b["n"]) if b["n"] else None,
                    "styles": [{"key": k, "label": linking.STYLES[k], "n": b["styles"].get(k, 0), "examples": b["examples"].get(k, [])} for k in linking.STYLES]})
    return out


def documents_page(request):
    repos = _repo_stats()
    tot = {k: sum(r[k] for r in repos) for k in ("files", "linked", "low", "unlinked", "changed", "text_ok", "text_failed", "folders")}
    tot["linked_pct"] = (tot["linked"] / tot["files"]) if tot["files"] else None
    findings = fetch_dict("SELECT severity, COUNT(*) n FROM documents_docfinding WHERE status = 'open' GROUP BY 1")
    fmap = {"error": 0, "warn": 0, "info": 0}
    fmap.update({r["severity"]: r["n"] for r in findings})
    fmap["serious"] = fmap["error"] + fmap["warn"]
    recent = loaders.recent_changes(14, 30)
    last_run = fetch_dict("SELECT MAX(last_indexed) t FROM documents_repo")[0]["t"]
    share = next((r for r in repos if r["is_share"]), None)
    return render(request, "documents/documents_page.html", _ctx(
        request, "documents", repos=repos, tot=tot, findings=fmap, recent=recent, hygiene=_hygiene(), last_run=last_run, share=share,
        kinds=KINDS, rules=linking.RULES, low=LOW_CONFIDENCE, styles=linking.STYLES,
        preset={k: request.GET.get(k, "") for k in ("repo", "project", "bid", "customer", "link", "kind", "age", "q", "text", "findings")}))


def documents_json(request):
    """Every live file (no row cap — the page filters, facets and sorts client-side), optionally narrowed by the
    same keys the page accepts (?repo=, ?project=, ?bid=, ?customer=, ?link=unlinked|check|linked, ?q=)."""
    where, params = ["NOT f.is_deleted"], []
    if request.GET.get("repo"):
        where.append("r.key = %s"); params.append(request.GET["repo"])
    if request.GET.get("project"):
        where.append("p.canonical_project_number = %s"); params.append(request.GET["project"].upper())
    if request.GET.get("bid"):
        where.append("b.id = %s"); params.append(int(request.GET["bid"]))
    if request.GET.get("customer"):
        where.append("(c.sl_customer_id = %s OR p.customer_id = (SELECT id FROM core_customer WHERE sl_customer_id = %s))"); params += [request.GET["customer"], request.GET["customer"]]
    link = request.GET.get("link")
    if link == "unlinked":
        where.append("f.linked_project_id IS NULL AND f.linked_bid_id IS NULL AND f.linked_customer_id IS NULL")
    elif link == "check":
        where.append("(f.linked_project_id IS NOT NULL OR f.linked_bid_id IS NOT NULL) AND f.link_confidence < %s"); params.append(LOW_CONFIDENCE)
    elif link == "linked":
        where.append("(f.linked_project_id IS NOT NULL OR f.linked_bid_id IS NOT NULL OR f.linked_customer_id IS NOT NULL)")
    if request.GET.get("q"):
        like = "%" + request.GET["q"].replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(f.name ILIKE %s OR f.path ILIKE %s OR p.title ILIKE %s OR p.display_number ILIKE %s)"); params += [like] * 4
    rows = fetch_dict("""
        SELECT f.id, f.name, f.ext, f.size, f.mtime, f.modified_by, f.path, f.web_url, f.unc, f.text_status, f.link_rule, f.link_confidence, f.link_via,
               f.linked_customer_id, r.name repo, r.key rkey, r.kind, r.division rdiv,
               p.display_number pnum, p.canonical_project_number cpn, p.title ptitle, d.code pdiv, p.lifecycle_state pstate,
               b.id bid_id, b.project_name bname, b.client_name bclient, b.division bdiv,
               c.canonical_name cname, c.sl_customer_id ckey,
               (SELECT COUNT(*) FROM documents_docfinding x WHERE x.file_id = f.id AND x.status = 'open' AND x.severity <> 'info') nfind
        FROM documents_file f JOIN documents_repo r ON r.id = f.repo_id
        LEFT JOIN core_project p ON p.id = f.linked_project_id LEFT JOIN core_division d ON d.id = p.division_id
        LEFT JOIN bids_bid b ON b.id = f.linked_bid_id LEFT JOIN core_customer c ON c.id = f.linked_customer_id
        WHERE %s ORDER BY f.mtime DESC NULLS LAST""" % " AND ".join(where), params)
    now = timezone.now()
    out = []
    for r in rows:
        has = bool(r["cpn"] or r["bid_id"] or r["linked_customer_id"])
        folder = r["path"].rsplit("/", 1)[0] if "/" in r["path"] else ""
        age = (now - r["mtime"]).days if r["mtime"] else None
        out.append({
            "id": r["id"], "name": r["name"], "ext": (r["ext"] or "").lower(), "kind": kind_of(r["ext"]), "size": r["size"],
            "mtime": r["mtime"].isoformat() if r["mtime"] else None, "age": age, "by": r["modified_by"] or "",
            "repo": r["repo"], "rkey": r["rkey"], "loc": "P: drive" if r["kind"] == "share" else "SharePoint",
            "div": r["pdiv"] or r["bdiv"] or r["rdiv"] or "", "folder": folder, "path": r["path"],
            "proj": r["pnum"] or "", "cpn": r["cpn"] or "", "ptitle": r["ptitle"] or "", "pstate": r["pstate"] or "",
            "bid": r["bid_id"], "bname": ((r["bclient"] + " · ") if r["bclient"] else "") + (r["bname"] or "") if r["bid_id"] else "",
            "cust": r["cname"] or "", "ckey": r["ckey"] or "",
            "conf": float(r["link_confidence"]) if r["link_confidence"] is not None else None, "rule": r["link_rule"] or "", "via": r["link_via"] or "",
            "link": _link_state(r["link_confidence"], has), "text": r["text_status"], "nfind": r["nfind"],
            "url": reverse("documents:document_preview", args=[r["id"]]),
            "open": r["web_url"] or ("" if r["kind"] != "share" else loaders.share_client.smb_url(r["path"])), "unc": r["unc"] or "",
            "purl": reverse("dashboard:project_detail", args=[r["cpn"]]) if r["cpn"] else "",
            "burl": reverse("bids:bid_detail", args=[r["bid_id"]]) if r["bid_id"] else "",
        })
    return JsonResponse({"rows": out, "n": len(out), "generated": now.isoformat(), "low": LOW_CONFIDENCE})


# ------------------------------------------------------------------------------------------- preview + content
def _folder_chain(folder):
    chain, seen = [], set()
    while folder is not None and folder.id not in seen:
        seen.add(folder.id)
        chain.append(folder)
        folder = folder.parent
    return chain


def document_preview(request, pk):
    f = get_object_or_404(File.objects.select_related("repo", "folder", "linked_project", "linked_bid", "linked_customer"), pk=pk)
    chain = _folder_chain(f.folder)
    own_links = list(f.links.select_related("project", "bid", "customer").order_by("-state", "-confidence"))
    folder_links = list(DocLink.objects.filter(folder__in=chain).select_related("folder", "project", "bid", "customer").order_by("-confidence"))
    try:
        dt = f.text
    except DocText.DoesNotExist:
        dt = None
    full = request.GET.get("full") == "1"
    text_shown = (dt.text if full or (dt and dt.chars <= 30000) else dt.text[:30000]) if dt else ""
    findings = list(f.findings.select_related("bid").order_by("status"))
    findings.sort(key=lambda x: (x.status != "open", SEVERITY_ORDER.get(x.severity, 9), x.check_id))
    ext = (f.ext or "").lower()
    preview = "none"
    if f.size <= CONTENT_CAP and not f.is_deleted:
        if ext == "pdf":
            preview = "pdf"
        elif ext in previewer.BROWSER_IMAGE_EXTS:
            preview = "image"
        elif previewer.kind(ext) == "pdf":
            preview = "office"
        elif previewer.kind(ext) == "html":      # Word / RTF / Excel / CSV rendered to HTML in a sandboxed frame
            preview = "html"
        elif previewer.kind(ext) == "image":     # QuickLook first page (pptx, xls, vsdx, heic …)
            preview = "page"
        elif dt is not None:
            preview = "text"
    related = File.objects.filter(is_deleted=False, folder=f.folder).exclude(pk=f.pk).order_by("-mtime")[:40] if f.folder_id else []
    # "← Back": the page that linked here (same host only), so a click from the Pipeline Snapshot returns to that exact
    # window and filters; the browser history is the fallback when there is no usable referrer
    from urllib.parse import urlsplit
    ref = request.META.get("HTTP_REFERER", "")
    rp = urlsplit(ref)
    back_url = (rp.path + ("?" + rp.query if rp.query else "")) if ref and rp.netloc == request.get_host() and rp.path != request.path else ""
    return render(request, "documents/document_preview.html", _ctx(
        request, "documents", f=f, chain=chain, own_links=own_links, folder_links=folder_links, text=dt, text_shown=text_shown, full=full,
        findings=findings, preview=preview, related=related, kind=kind_of(ext), rules=linking.RULES, checks=checks.CHECKS, low=LOW_CONFIDENCE,
        next_url=request.get_full_path(), can_link=request.acc.can("documents.findings"), can_findings=request.acc.can("documents.findings"), back_url=back_url,
        smb=loaders.share_client.smb_url(f.path) if f.repo.is_share else ""))


def _cache_path(f, suffix=None):
    d = Path(settings.APP_SUPPORT_DIR) / "doc_cache" / str(f.repo_id)
    d.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha1((f.etag or f.path).encode("utf8", "replace")).hexdigest()[:12]
    ext = (f.ext or "bin").lower()[:10]
    return d / ("%d_%s.%s" % (f.id, tag, suffix or ext))


def _cached_source(f):
    """The file's bytes on disk (fetched through the read-only clients once), or an error JsonResponse."""
    if f.size and f.size > CONTENT_CAP:
        return None, JsonResponse({"error": "file larger than the %d MB preview cap — open it in SharePoint / on the P: drive" % (CONTENT_CAP // 1024 // 1024)}, status=413)
    path = _cache_path(f)
    if not path.exists():
        try:
            data = loaders.content_bytes(f, CONTENT_CAP)
        except Exception as e:  # noqa
            return None, JsonResponse({"error": "could not fetch the file: %s" % str(e)[:200]}, status=502)
        if data is None:
            return None, JsonResponse({"error": "file over the size cap"}, status=413)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as out:
            out.write(data)
        os.replace(tmp, path)
    return path, None


@xframe_options_sameorigin
def document_pdf(request, pk):
    """Cached, full-layout Office preview; the original remains available to download."""
    if not getattr(request, "acc", None) or not request.acc.can("documents.view"):
        raise Http404
    f = get_object_or_404(File.objects.select_related("repo"), pk=pk)
    if f.is_deleted or previewer.kind(f.ext) != "pdf":
        raise Http404
    src, err = _cached_source(f)
    if err is not None:
        return err
    out = _cache_path(f, "preview-v1.pdf")
    if not out.exists() and not previewer.to_pdf(str(src), f.ext, str(out)):
        markup = previewer.to_html(str(src), f.ext)
        resp = HttpResponse(markup or "This file could not be converted for preview. Try downloading the original.",
                            content_type="text/html; charset=utf-8", status=200 if markup else 422)
        resp["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    else:
        resp = FileResponse(open(out, "rb"), content_type="application/pdf")
        resp["Content-Disposition"] = 'inline; filename="preview.pdf"'
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=0"
    return resp


@xframe_options_sameorigin
def document_html(request, pk):
    """Word / RTF / Excel / CSV rendered to HTML (apps.documents.preview), cached, served for the preview page's
    sandboxed frame: no scripts can run (CSP sandbox + the frame's sandbox attribute), nothing external is loaded."""
    if not getattr(request, "acc", None) or not request.acc.can("documents.view"):
        raise Http404
    f = get_object_or_404(File.objects.select_related("repo"), pk=pk)
    if f.is_deleted or previewer.kind(f.ext) != "html":
        raise Http404
    src, err = _cached_source(f)
    if err is not None:
        return err
    out = _cache_path(f, "preview.html")
    if not out.exists():
        markup = previewer.to_html(str(src), f.ext)
        if markup is None:
            return HttpResponse("<!doctype html><meta charset='utf-8'><body style='font:13px -apple-system,sans-serif;color:#5d6b7c;padding:18px'>This file could not be converted for preview — open it at the source.</body>", content_type="text/html; charset=utf-8", status=200)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(markup, encoding="utf-8")
        os.replace(tmp, out)
    resp = FileResponse(open(out, "rb"), content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=0"
    return resp


@xframe_options_sameorigin
def document_page(request, pk):
    """First page of anything QuickLook can draw (pptx, xls, vsdx, heic …) as a PNG, cached."""
    if not getattr(request, "acc", None) or not request.acc.can("documents.view"):
        raise Http404
    f = get_object_or_404(File.objects.select_related("repo"), pk=pk)
    if f.is_deleted or previewer.kind(f.ext) != "image":
        raise Http404
    src, err = _cached_source(f)
    if err is not None:
        return err
    out = _cache_path(f, "page.png")
    if not out.exists() and not previewer.to_png(str(src), f.ext, str(out)):
        return JsonResponse({"error": "This image could not be converted for preview — open it at the source"}, status=422)
    resp = FileResponse(open(out, "rb"), content_type="image/png")
    resp["Cache-Control"] = "private, max-age=0"
    return resp


@xframe_options_sameorigin          # the preview page frames PDFs / images from this same origin; DENY elsewhere stays
def document_content(request, pk):
    """Stream the file through PCA (Graph /content or the read-only share), cached on disk. documents.view is
    enforced by the middleware from the registry; it is checked again here because this endpoint hands out bytes."""
    if not getattr(request, "acc", None) or not request.acc.can("documents.view"):
        raise Http404
    f = get_object_or_404(File.objects.select_related("repo"), pk=pk)
    if f.is_deleted:
        raise Http404
    path, err = _cached_source(f)
    if err is not None:
        return err
    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    ext = (f.ext or "").lower()
    inline = ext in (extract.PREVIEW_EXTS | previewer.BROWSER_IMAGE_EXTS) and request.GET.get("download") != "1"
    if ext in ("txt", "md", "csv", "log"):
        ctype = "text/plain; charset=utf-8"
    resp = FileResponse(open(path, "rb"), content_type=ctype)
    resp["Content-Disposition"] = '%s; filename="%s"' % ("inline" if inline else "attachment", f.name.replace('"', ""))
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=0"
    return resp


# ------------------------------------------------------------------------------------------- findings queue
def documents_findings(request):
    if request.method == "POST":
        if not request.acc.can("documents.findings"):
            raise Http404
        fi = get_object_or_404(DocFinding, pk=request.POST.get("finding"))
        status = request.POST.get("status")
        if status in dict(DocFinding.Status.choices):
            fi.status = status
            fi.decided_by = request.user if request.user.is_authenticated else None
            fi.decided_at = timezone.now()
            fi.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
            audit_log("document_finding", request, target=None, finding=fi.id, status=status, file=fi.file_id, check=fi.check_id)
        if request.GET.get("format") == "json" or request.headers.get("Accept", "").startswith("application/json"):
            return JsonResponse({"ok": True, "status": fi.status})
        return redirect(request.POST.get("next") or reverse("documents:documents_findings"))
    status = request.GET.get("status", "open")
    qs = DocFinding.objects.select_related("file", "file__repo", "bid", "bid__estimator", "project").order_by("-created_at")
    if status == "open":
        qs = qs.filter(status__in=["open", "acknowledged"])
    elif status in dict(DocFinding.Status.choices):
        qs = qs.filter(status=status)
    rows = list(qs)
    sev = request.GET.get("severity", "warn,error")
    sevs = [s for s in sev.split(",") if s] if sev != "all" else []
    facets = {"severity": Counter(), "check": Counter(), "bid": Counter(), "estimator": Counter(), "status": Counter()}
    for x in rows:
        facets["severity"][x.severity] += 1
        facets["check"][x.check_id] += 1
        facets["status"][x.status] += 1
        if x.bid_id:
            facets["bid"][x.bid_id] += 1
            facets["estimator"][x.bid.estimator.canonical_name if x.bid.estimator_id else (x.bid.bidder_raw or "—")] += 1
    check, bid, est, fid = request.GET.get("check"), request.GET.get("bid"), request.GET.get("estimator"), request.GET.get("file")
    shown = [x for x in rows if (not sevs or x.severity in sevs) and (not check or x.check_id == check) and (not bid or str(x.bid_id) == bid)
             and (not fid or str(x.file_id) == fid)
             and (not est or (x.bid_id and (x.bid.estimator.canonical_name if x.bid.estimator_id else (x.bid.bidder_raw or "—")) == est))]
    shown.sort(key=lambda x: (x.status != "open", SEVERITY_ORDER.get(x.severity, 9), -(x.created_at.timestamp())))
    bids = {}
    for x in rows:
        if x.bid_id and x.bid_id not in bids:
            bids[x.bid_id] = x.bid
    facets = {k: dict(v.most_common()) for k, v in facets.items()}   # plain dicts: a Counter answers `.items` with 0 in a template
    return render(request, "documents/documents_findings.html", _ctx(
        request, "documents", rows=shown, total=len(rows), facets=facets, bids=bids, checks=checks.CHECKS, status=status, sevs=sevs, sev=sev,
        check=check, bid=bid, est=est, fid=fid, can_findings=request.acc.can("documents.findings"), next_url=request.get_full_path(),
        standard=loaders.standard_clauses(), model_review=checks.MODEL_REVIEW_ENABLED))


# ------------------------------------------------------------------------------------------- link state (PCA-owned)
@require_POST
def document_link_state(request):
    """Confirm / reject an automatic link, or add a manual one. documents.findings (registry); audited."""
    if not request.acc.can("documents.findings"):
        raise Http404
    P = request.POST
    result = {}
    touched_files, touched_folders = [], []
    if P.get("link"):
        l = get_object_or_404(DocLink, pk=P["link"])
        state = P.get("state")
        if state not in dict(DocLink.State.choices):
            return JsonResponse({"error": "bad state"}, status=400)
        if state == DocLink.State.AUTO and l.rule == "manual":
            l.delete(); result = {"deleted": True}
        else:
            l.state = state
            l.decided_by = request.user if request.user.is_authenticated else None
            l.decided_at = timezone.now()
            l.save(update_fields=["state", "decided_by", "decided_at", "updated_at"])
            result = {"link": l.id, "state": l.state}
        (touched_files if l.file_id else touched_folders).append(l.file_id or l.folder_id)
        audit_log("document_link", request, link=l.id, state=state, file=l.file_id, folder=l.folder_id, rule=l.rule)
    elif P.get("file") or P.get("folder"):
        subj_file = get_object_or_404(File, pk=P["file"]) if P.get("file") else None
        subj_folder = get_object_or_404(Folder, pk=P["folder"]) if P.get("folder") else None
        pid = bid = cid = None
        if P.get("project"):
            proj = Project.objects.filter(canonical_project_number=P["project"].strip().upper()).first()
            if proj is None:
                return JsonResponse({"error": "no SL project %s" % P["project"]}, status=400)
            pid = proj.id
        elif P.get("bid"):
            bid = int(P["bid"])
        elif P.get("customer"):
            cust = Customer.objects.filter(sl_customer_id=P["customer"].strip()).first()
            if cust is None:
                return JsonResponse({"error": "no customer %s" % P["customer"]}, status=400)
            cid = cust.id
        else:
            return JsonResponse({"error": "project, bid or customer required"}, status=400)
        key = DocLink.dedupe_key(subj_file.id if subj_file else None, subj_folder.id if subj_folder else None, pid, bid, cid)
        l, created = DocLink.objects.get_or_create(dedupe=key, defaults={"file": subj_file, "folder": subj_folder, "project_id": pid, "bid_id": bid, "customer_id": cid,
                                                                         "rule": "manual", "confidence": 1, "state": DocLink.State.CONFIRMED,
                                                                         "evidence": {"by": getattr(request.user, "email", "")}})
        if not created:
            l.state, l.rule, l.confidence = DocLink.State.CONFIRMED, l.rule if l.rule != "manual" else "manual", max(l.confidence, 1) if l.rule == "manual" else l.confidence
            l.decided_by = request.user if request.user.is_authenticated else None
            l.decided_at = timezone.now()
            l.save()
        result = {"link": l.id, "state": l.state, "created": created}
        (touched_files if subj_file else touched_folders).append(subj_file.id if subj_file else subj_folder.id)
        audit_log("document_link", request, link=l.id, state="manual", file=subj_file.id if subj_file else None, folder=subj_folder.id if subj_folder else None)
    else:
        return JsonResponse({"error": "link or file/folder required"}, status=400)
    result.update(loaders.apply_effective_links(file_ids=touched_files, folder_ids=touched_folders))
    if request.GET.get("format") == "json" or request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse(result)
    return redirect(P.get("next") or reverse("documents:documents_page"))
