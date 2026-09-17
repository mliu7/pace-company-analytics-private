"""Global-search hook: the Files group (SharePoint spec §7.2) — file names from the document index and, where text
has been extracted, a hit inside the text. Items are shaped like the other groups in apps/dashboard/search.py
(type 'file', key, url, title, sub, ava, tags, meta, score). Only called when the viewer holds documents.view."""

import re
from datetime import date

from django.urls import reverse

from apps.dashboard.search import _all_tokens, _like, _when, score_text
from apps.ingestion.bulk import fetch_dict

CAP = 400
KIND_AVA = {"pdf": "PDF", "docx": "DOC", "doc": "DOC", "xlsx": "XLS", "xlsm": "XLS", "xls": "XLS", "msg": "✉", "txt": "TXT", "pptx": "PPT",
            "jpg": "IMG", "jpeg": "IMG", "png": "IMG", "gif": "IMG", "dwg": "DWG", "vsdx": "VSD", "one": "ONE", "zip": "ZIP"}


def _snippet(text, tokens, width=110):
    low = (text or "").lower()
    pos = -1
    for t in tokens:
        pos = low.find(t)
        if pos >= 0:
            break
    if pos < 0:
        return ""
    a, b = max(0, pos - width // 3), min(len(text), pos + width)
    return ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")


def group(q, tokens, acc, limit=6):
    """([items], count) for the Files group — names first, then text hits; ([], 0) when nothing matches."""
    q = (q or "").strip()
    if not tokens:
        return [], 0
    frag, params = _all_tokens("f.name", tokens)
    sql = """
        SELECT f.id, f.name, f.ext, f.path, f.mtime, f.modified_by, f.link_confidence conf, r.name repo, r.kind,
               p.display_number pnum, p.canonical_project_number cpn, p.title ptitle, b.project_name bname, NULL::text snippet
        FROM documents_file f JOIN documents_repo r ON r.id = f.repo_id
        LEFT JOIN core_project p ON p.id = f.linked_project_id LEFT JOIN bids_bid b ON b.id = f.linked_bid_id
        WHERE NOT f.is_deleted AND (%s OR f.path ILIKE %%s)
        ORDER BY f.mtime DESC NULLS LAST LIMIT %d""" % (frag, CAP)
    rows = fetch_dict(sql, params + [_like(q)])
    capped = len(rows) >= CAP
    seen = {r["id"] for r in rows}
    if len(q) >= 4 and len(rows) < CAP:
        tfrag, tparams = _all_tokens("t.text", tokens)
        rows += fetch_dict("""
            SELECT f.id, f.name, f.ext, f.path, f.mtime, f.modified_by, f.link_confidence conf, r.name repo, r.kind,
                   p.display_number pnum, p.canonical_project_number cpn, p.title ptitle, b.project_name bname,
                   substr(t.text, GREATEST(1, position(lower(%%s) in lower(t.text)) - 40), 160) snippet
            FROM documents_doctext t JOIN documents_file f ON f.id = t.file_id JOIN documents_repo r ON r.id = f.repo_id
            LEFT JOIN core_project p ON p.id = f.linked_project_id LEFT JOIN bids_bid b ON b.id = f.linked_bid_id
            WHERE NOT f.is_deleted AND %s AND NOT (f.id = ANY(%%s))
            ORDER BY f.mtime DESC NULLS LAST LIMIT 60""" % tfrag, [tokens[0]] + tparams + [list(seen)])
    out = []
    today = date.today()
    for r in rows:
        s = score_text(r["name"], tokens)
        note = ""
        if not s:
            if r["snippet"]:
                s, note = 58, _snippet(r["snippet"], tokens)
            elif q.lower() in (r["path"] or "").lower():
                s, note = 50, "in folder " + (r["path"].rsplit("/", 1)[0] if "/" in r["path"] else "")
            else:
                continue
        if r["mtime"] and (today - r["mtime"].date()).days <= 120:
            s += 4
        where = "P: drive" if r["kind"] == "share" else r["repo"]
        job = (r["pnum"] + " " + (r["ptitle"] or "")).strip() if r["pnum"] else (("bid " + r["bname"]) if r["bname"] else "")
        sub = " · ".join(x for x in (where, job, note) if x)
        tags = []
        if r["pnum"]:
            tags.append({"t": r["pnum"], "c": "in_progress" if (r["conf"] or 0) >= 0.8 else "dormant"})
        elif r["bname"]:
            tags.append({"t": "bid", "c": "awarded_not_started"})
        out.append({"type": "file", "key": str(r["id"]), "url": reverse("documents:document_preview", args=[r["id"]]), "number": r["pnum"] or "",
                    "title": r["name"], "sub": sub, "ava": KIND_AVA.get((r["ext"] or "").lower(), (r["ext"] or "?").upper()[:3]), "tags": tags,
                    "meta": _when(r["mtime"].date()) if r["mtime"] else "", "score": s,
                    "where": where, "job": job, "cpn": r["cpn"] or "", "path": r["path"], "by": r["modified_by"] or "", "last": r["mtime"], "note": note})
    out.sort(key=lambda x: (-x["score"], x["title"]))
    return out, (len(out) if not capped else CAP)
