"""Loaders for the document index (SharePoint spec §7, §12, §13): the allowlisted SharePoint libraries through Graph
delta (stored deltaLinks), the P: drive through the read-only share walker (incremental by size + mtime), the linking
rules (apps/documents/linking.py) with PCA-owned confirm / reject state, bounded text extraction, the proposal checks
and the Data Quality issues. Read-only against every source; writes only PCA's own tables.

`refresh_all_step(run)` is what refresh_all calls (best-effort, bounded per run); `manage.py refresh_documents` runs
the same steps by hand with --full / --extract / --checks / --share-root.
"""

import logging
import re
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.bids.models import Bid
from apps.core.models import Project
from apps.ingestion.bulk import content_hash, fetch_dict
from apps.ingestion.loaders import issue
from apps.ingestion.sources import graph_client as g
from apps.ingestion.sources import share_client

from . import checks, extract, linking
from .models import DocFinding, DocLink, DocText, File, Folder, ListAttachment, Repo

log = logging.getLogger(__name__)

# The allowlisted SharePoint libraries (spec §1.1, §7.1). Add a row to index another library; `key` must stay stable.
LIBRARIES = [
    {"key": "sp:/sites/PremiseSecurity:Documents", "site": "/sites/PremiseSecurity", "library": "Documents", "name": "Premise Security › Documents", "division": "070"},
    {"key": "sp:/sites/TEST:Documents", "site": "/sites/TEST", "library": "Documents", "name": "AV Division › Documents", "division": "040"},
    {"key": "sp:/sites/AVTEAM:Documents", "site": "/sites/AVTEAM", "library": "Documents", "name": "AV TEAM › Documents", "division": "040"},
    {"key": "sp:/sites/SSPM:Documents", "site": "/sites/SSPM", "library": "Documents", "name": "SS PM › Documents", "division": "040"},
    {"key": "sp:/sites/OTAVquotesandproposals:Documents", "site": "/sites/OTAVquotesandproposals", "library": "Documents", "name": "OT AV quotes and proposals › Documents", "division": "040"},
    {"key": "sp:/sites/ProjectPortal:Project Folders", "site": "/sites/ProjectPortal", "library": "Project Folders", "name": "Project Portal › Project Folders", "division": ""},
]
SHARE_REPO = {"key": "share:projects", "name": "P: drive (%s)" % share_client.SHARE_UNC_ROOT, "division": ""}
EXTRACT_LIMIT, EXTRACT_BUDGET_S, CHECK_LIMIT = 150, 150, 300
PROPOSAL_NAME_WORDS = ("proposal", "quote", "quotation", "bid", "pricing", "estimate")
OPEN_STATES = ("awarded_not_started", "in_progress", "field_complete", "dormant")
_MAX = {f.name: f.max_length for f in File._meta.get_fields() if getattr(f, "max_length", None)}


def _s(v, n=None):
    s = "" if v is None else str(v).strip()
    return s[:n] if n else s


def _ext(name):
    return name.rsplit(".", 1)[1].lower()[:16] if "." in name and not name.endswith(".") else ""


# ------------------------------------------------------------------------------------------- repos
def ensure_repos(with_share=True, share_root=None):
    """Repo rows for every allowlisted library (drive ids resolved through Graph once) and the share root."""
    out = []
    for lib in LIBRARIES:
        repo, _ = Repo.objects.get_or_create(key=lib["key"], defaults={"kind": Repo.Kind.SHAREPOINT, "name": lib["name"], "site_path": lib["site"], "division": lib["division"]})
        changed = False
        if not repo.drive_id:
            try:
                site = g.site(lib["site"])
                drive = next((d for d in g.drives(site["id"]) if d.get("name") == lib["library"]), None)
                if drive is None:
                    repo.last_error = "library %r not found on %s" % (lib["library"], lib["site"])
                else:
                    repo.drive_id, repo.web_url, repo.last_error = drive["id"], _s(drive.get("webUrl"), 500), ""
                changed = True
            except g.GraphError as e:
                repo.last_error = str(e)[:500]; changed = True
        if repo.name != lib["name"] or repo.division != lib["division"]:
            repo.name, repo.division, changed = lib["name"], lib["division"], True
        if changed:
            repo.save()
        out.append(repo)
    if with_share:
        root = str(share_client.root(share_root))
        repo, _ = Repo.objects.get_or_create(key=SHARE_REPO["key"], defaults={"kind": Repo.Kind.SHARE, "name": SHARE_REPO["name"], "root_path": root})
        if repo.root_path != root:
            repo.root_path = root; repo.save(update_fields=["root_path"])
        out.append(repo)
    return out


# ------------------------------------------------------------------------------------------- SharePoint libraries
def _rel_path(parent_path):
    """'/drives/<id>/root:/Active Jobs/265092 - x' -> 'Active Jobs/265092 - x'; root children -> ''."""
    if not parent_path or "root:" not in parent_path:
        return ""
    return parent_path.split("root:", 1)[1].strip("/")


def _who(it):
    u = (it.get("lastModifiedBy") or {}).get("user") or (it.get("lastModifiedBy") or {}).get("application") or {}
    return _s(u.get("displayName") or u.get("email"), 128)


def index_library(repo, run, full=False):
    """Graph delta on one library -> Folder / File rows (renames re-path descendants, deletions mark is_deleted).
    Returns stats incl. the ids touched, for the linker."""
    if not repo.drive_id:
        raise g.GraphError("repo %s has no drive id (%s)" % (repo.key, repo.last_error))
    label = "docs_" + repo.key.replace("/", "_").replace(":", "_")
    link = None if full else (repo.delta_link or None)
    try:
        items, new_link = g.drive_delta(repo.drive_id, link, label=label)
    except g.GraphError as e:
        if link and e.status in (400, 410):        # resyncRequired / expired token: start over
            items, new_link = g.drive_delta(repo.drive_id, None, label=label)
            full = True
        else:
            raise
    stats = Counter(items=len(items))
    folders_in = [it for it in items if it.get("folder") is not None and it.get("name") != "root" and not it.get("root")]
    files_in = [it for it in items if it.get("file") is not None]
    deleted_ids = {it["id"] for it in items if it.get("deleted")}
    now = timezone.now()
    touched_folders, touched_files = [], []
    with transaction.atomic():
        # folders first, shallow to deep, so parents exist
        folders_in.sort(key=lambda it: _rel_path((it.get("parentReference") or {}).get("path")).count("/") + (1 if _rel_path((it.get("parentReference") or {}).get("path")) else 0))
        existing = {f.item_id: f for f in Folder.objects.filter(repo=repo, item_id__in=[it["id"] for it in folders_in])}
        parent_ids = {(it.get("parentReference") or {}).get("id") for it in folders_in + files_in} - {None}
        parents = {f.item_id: f for f in Folder.objects.filter(repo=repo, item_id__in=parent_ids)}
        for it in folders_in:
            pref = it.get("parentReference") or {}
            rel_parent = _rel_path(pref.get("path"))
            path = (rel_parent + "/" + it["name"]) if rel_parent else it["name"]
            f = existing.get(it["id"])
            fields = {"parent": parents.get(pref.get("id")), "path": path[:1000], "name": _s(it["name"], 255), "depth": path.count("/") + 1,
                      "mtime": parse_datetime(it["lastModifiedDateTime"]) if it.get("lastModifiedDateTime") else None,
                      "web_url": _s(it.get("webUrl"), 1000), "is_deleted": it["id"] in deleted_ids, "last_seen_run": run}
            if f is None:
                f = Folder.objects.create(repo=repo, item_id=it["id"], **fields)
                stats["folders_new"] += 1
            else:
                old_path = f.path
                changed = any(getattr(f, k) != v for k, v in fields.items() if k != "last_seen_run")
                for k, v in fields.items():
                    setattr(f, k, v)
                f.save()
                if changed:
                    stats["folders_updated"] += 1
                if old_path != f.path and not f.is_deleted:
                    _repath_descendants(repo, old_path, f.path)
                    stats["repathed"] += 1
            parents[f.item_id] = f
            existing[f.item_id] = f
            touched_folders.append(f.id)
        existing_files = {f.item_id: f for f in File.objects.filter(repo=repo, item_id__in=[it["id"] for it in files_in])}
        for it in files_in:
            pref = it.get("parentReference") or {}
            rel_parent = _rel_path(pref.get("path"))
            path = (rel_parent + "/" + it["name"]) if rel_parent else it["name"]
            f = existing_files.get(it["id"])
            fields = {"folder": parents.get(pref.get("id")), "path": path[:1000], "name": _s(it["name"], 255), "ext": _ext(it["name"]),
                      "size": int(it.get("size") or 0), "mtime": parse_datetime(it["lastModifiedDateTime"]) if it.get("lastModifiedDateTime") else None,
                      "created": parse_datetime(it["createdDateTime"]) if it.get("createdDateTime") else None, "modified_by": _who(it),
                      "etag": _s(it.get("eTag"), 128), "web_url": _s(it.get("webUrl"), 1000), "unc": "", "is_deleted": it["id"] in deleted_ids,
                      "last_seen_run": run, "indexed_at": now}
            if f is None:
                f = File.objects.create(repo=repo, item_id=it["id"], **fields)
                stats["files_new"] += 1
            else:
                changed = f.etag != fields["etag"] or f.path != fields["path"] or f.is_deleted != fields["is_deleted"] or f.folder_id != (fields["folder"].id if fields["folder"] else None)
                if changed:
                    if f.etag != fields["etag"]:
                        f.text_status = File.TextStatus.NONE; f.text_error = ""
                    for k, v in fields.items():
                        setattr(f, k, v)
                    f.save()
                    stats["files_updated"] += 1
                else:
                    File.objects.filter(pk=f.pk).update(last_seen_run=run)
            touched_files.append(f.id)
        # deletions of items we only know by id (a deleted folder's children come as their own deleted items)
        gone_folders = deleted_ids - set(existing) - {it["id"] for it in files_in}
        if gone_folders:
            Folder.objects.filter(repo=repo, item_id__in=gone_folders).update(is_deleted=True)
            File.objects.filter(repo=repo, item_id__in=gone_folders).update(is_deleted=True)
        repo.delta_link = new_link or repo.delta_link
        repo.last_indexed, repo.last_error = now, ""
        repo.file_count = File.objects.filter(repo=repo, is_deleted=False).count()
        repo.folder_count = Folder.objects.filter(repo=repo, is_deleted=False).count()
        repo.save()
    stats["deleted"] = len(deleted_ids)
    return dict(stats, touched_folders=touched_folders, touched_files=touched_files)


def _repath_descendants(repo, old_path, new_path):
    """A renamed / moved folder: Graph delta returns the folder only, so the children's stored paths are rewritten."""
    from django.db import connection
    with connection.cursor() as cur:
        for table in ("documents_folder", "documents_file"):
            cur.execute("""UPDATE %s SET path = %%s || substr(path, %%s) WHERE repo_id = %%s AND path LIKE %%s""" % table,
                        [new_path, len(old_path) + 1, repo.id, old_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"])
        cur.execute("UPDATE documents_folder SET depth = array_length(string_to_array(path, '/'), 1) WHERE repo_id = %s AND path LIKE %s",
                    [repo.id, new_path.replace("%", "\\%").replace("_", "\\_") + "/%"])


# ------------------------------------------------------------------------------------------- the share
SHARE_BUDGET_S = 240


def _share_upsert(repo, run, entries, existing_folders, existing_files, stats, touched_folders, touched_files, now):
    """Folder / File rows for one batch of walk entries (folders arrive before their contents). The dicts are keyed by
    `share_client.item_key(rel)` (= the relative path, hashed past 255 characters), the same value as `item_id`."""
    K = share_client.item_key
    seen_folders, seen_files, unchanged = set(), set(), []
    for e in entries:
        parent = existing_folders.get(K(e["parent_rel"])) if e["parent_rel"] else None
        if e["is_dir"]:
            seen_folders.add(K(e["rel"]))
            f = existing_folders.get(K(e["rel"]))
            if f is None:
                f = Folder.objects.create(repo=repo, item_id=K(e["rel"]), path=e["rel"][:1000], name=e["name"][:255], parent=parent,
                                          depth=e["depth"], mtime=e["mtime"], last_seen_run=run)
                existing_folders[K(e["rel"])] = f
                stats["folders_new"] += 1
                touched_folders.append(f.id)
            elif f.is_deleted or f.mtime != e["mtime"] or f.parent_id != (parent.id if parent else None):
                f.is_deleted, f.mtime, f.parent, f.depth, f.name, f.last_seen_run = False, e["mtime"], parent, e["depth"], e["name"][:255], run
                f.save()
                stats["folders_updated"] += 1
                touched_folders.append(f.id)
        else:
            seen_files.add(K(e["rel"]))
            sig = share_client.signature(e["size"], e["mtime"])
            row = existing_files.get(K(e["rel"]))
            if row is None:
                f = File.objects.create(repo=repo, item_id=K(e["rel"]), path=e["rel"][:1000], name=e["name"][:255], ext=_ext(e["name"]), size=e["size"],
                                        mtime=e["mtime"], folder=parent, etag=sig, unc=share_client.unc_path(e["rel"])[:1000], last_seen_run=run, indexed_at=now)
                stats["files_new"] += 1
                touched_files.append(f.id)
                existing_files[K(e["rel"])] = {"id": f.id, "item_id": f.item_id, "etag": sig, "is_deleted": False, "folder_id": parent.id if parent else None}
            elif row["etag"] != sig or row["is_deleted"] or row["folder_id"] != (parent.id if parent else None):
                File.objects.filter(pk=row["id"]).update(size=e["size"], mtime=e["mtime"], folder=parent, etag=sig, is_deleted=False, name=e["name"][:255],
                                                         ext=_ext(e["name"]), unc=share_client.unc_path(e["rel"])[:1000], last_seen_run=run, indexed_at=now,
                                                         text_status=File.TextStatus.NONE, text_error="")
                stats["files_updated"] += 1
                touched_files.append(row["id"])
            else:
                unchanged.append(row["id"])
                stats["files_unchanged"] += 1
    for i in range(0, len(unchanged), 5000):
        File.objects.filter(pk__in=unchanged[i:i + 5000]).update(last_seen_run=run)
    return seen_folders, seen_files


def _like_prefix(path):
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


SHARE_UNIT_DEPTH = 2     # the P: drive is '<year> Projects/<job folder>/…': a job folder is the unit of work
SHARE_FULL_REWALK_DAYS = 30   # a unit whose directory tree shows no change is walked in full again after this long


_YEAR_TOP = re.compile(r"^(\d{4}) Projects(?:/|$)")


def open_bid_client_names():
    """Lower-cased client names of the open Project List bids — the client folders a proposal lives under. The walk
    takes them first so a quote on the P: drive shows on its bid page within minutes, not after the whole drive."""
    try:
        return {(r["c"] or "").strip().lower() for r in fetch_dict("SELECT DISTINCT client_name c FROM bids_bid WHERE source = 'list' AND stage IN ('quoting', 'submitted', 'on_hold') AND client_name <> ''")}
    except Exception:  # noqa - the bids table may not exist in a stripped-down deployment
        return set()


def _unit_order(f, prefer=frozenset()):
    """Walk order for the P: drive units: (1) client folders of open bids under a `<year> Projects` tree, (2) folders never
    walked to the end, (3) newest year first, (4) the stalest walk, (5) name. Before 2026-09-10 the order was stalest-first
    alphabetical, which started at 2012 and would have reached the live proposals last."""
    m = _YEAR_TOP.match(f.path)
    year = int(m.group(1)) if m else 0
    return (not (m and f.name.strip().lower() in prefer), f.walked_at is not None, -year,
            f.walked_at or timezone.make_aware(timezone.datetime(2000, 1, 1)), f.path.lower())


def _mark_gone(repo, sub_folders, sub_files, seen_f, seen_x, keep, stats):
    gone = [r["id"] for rel, r in sub_files.items() if rel not in seen_x and not r["is_deleted"]]
    gone_f = [f.id for rel, f in sub_folders.items() if rel not in keep and rel not in seen_f and not f.is_deleted]
    if gone:
        File.objects.filter(pk__in=gone).update(is_deleted=True)
    if gone_f:
        Folder.objects.filter(pk__in=gone_f).update(is_deleted=True)
        File.objects.filter(repo=repo, folder__in=gone_f).update(is_deleted=True)
    stats["deleted"] += len(gone) + len(gone_f)


def walk_share(repo, run, share_root=None, budget_seconds=SHARE_BUDGET_S, unit_depth=SHARE_UNIT_DEPTH):
    """Incremental, budgeted walk of the P: drive. Every run re-lists the shallow levels (the root, then each folder
    down to `unit_depth`, i.e. the year folders' own files and job folders); then the units — job folders — are
    walked to the end stalest-first (Folder.walked_at) until the time budget is spent, one commit per unit. A file
    whose (size, mtime) signature is unchanged is only marked seen; folders / files no longer on disk are marked
    deleted only where a listing or a unit walk completed. Never writes to the share."""
    h = share_client.health(share_root)
    if not h["ok"]:
        repo.last_error = h["error"]; repo.save(update_fields=["last_error"])
        return {"skipped": h["error"], "root": h["root"]}
    # one walker at a time: refresh_all's step and a hand-run `refresh_documents --budget …` over the same share raced on
    # 2026-09-10 (duplicate-key on a folder both had just listed, and both crawled SMB at half speed). Postgres advisory
    # lock, session-scoped; the second walker skips and reports it instead of colliding.
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [SHARE_WALK_LOCK_KEY])
        if not cur.fetchone()[0]:
            note = "skipped: another share walk is running (advisory lock %d held)" % SHARE_WALK_LOCK_KEY
            log.warning("walk_share: %s", note)
            return {"skipped": note}
    try:
        return _walk_share_locked(repo, run, share_root, budget_seconds, unit_depth, h)
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [SHARE_WALK_LOCK_KEY])


SHARE_WALK_LOCK_KEY = 815071   # refresh_all's lock is 815070 (apps/ingestion/management/commands/refresh_all.py)


def _walk_share_locked(repo, run, share_root, budget_seconds, unit_depth, h):
    t0 = time.time()
    stats, errors = Counter(), []
    now = timezone.now()
    touched_folders, touched_files = [], []

    def on_error(rel, exc):
        errors.append((rel, str(exc)[:200]))

    def shallow(start_rel, depth):
        """Re-list one folder (its files + sub-folders only) and reconcile deletions among those direct children."""
        try:
            entries = [e for e in share_client.walk(share_root, max_depth=depth + 1, on_error=on_error, start_rel=start_rel)]
        except share_client.ShareUnavailable as e:
            errors.append((start_rel, str(e)[:200]))
            return []
        folders = {f.item_id: f for f in Folder.objects.filter(repo=repo, depth=depth + 1, path__startswith=(start_rel + "/") if start_rel else "")}
        if start_rel:
            folders = {k: v for k, v in folders.items() if v.path.rsplit("/", 1)[0] == start_rel}
            parent = Folder.objects.filter(repo=repo, item_id=share_client.item_key(start_rel)).first()
            if parent is not None:
                folders[share_client.item_key(start_rel)] = parent
            files = {r["item_id"]: r for r in fetch_dict("SELECT id, item_id, etag, is_deleted, folder_id FROM documents_file WHERE repo_id = %s AND folder_id = %s", [repo.id, parent.id if parent else -1])}
        else:
            files = {r["item_id"]: r for r in fetch_dict("SELECT id, item_id, etag, is_deleted, folder_id FROM documents_file WHERE repo_id = %s AND folder_id IS NULL", [repo.id])}
        with transaction.atomic():
            seen_f, seen_x = _share_upsert(repo, run, entries, folders, files, stats, touched_folders, touched_files, now)
            _mark_gone(repo, folders, files, seen_f, seen_x, {share_client.item_key(start_rel)}, stats)
        return [folders[share_client.item_key(e["rel"])] for e in entries if e["is_dir"] and share_client.item_key(e["rel"]) in folders]

    # ---- shallow levels: root, then every folder above the unit depth
    level = shallow("", 0)
    for d in range(1, unit_depth):
        nxt = []
        for fo in level:
            nxt += shallow(fo.path, d)
        level = nxt
    stats["units_listed"] = len(level)
    # ---- the units, stalest first. A unit walked to the end within SHARE_FULL_REWALK_DAYS whose own mtime did not move
    # this run is first checked with a directory-only listing (no file stats): when every sub-folder is still there with
    # the same mtime, nothing was added, removed or renamed anywhere inside it and the unit is verified without a full
    # walk — the steady-state pass over the drive is then a fraction of the first one.
    touched_set = set(touched_folders)
    prefer = open_bid_client_names()
    units = sorted((f for f in level if not f.is_deleted), key=lambda f: _unit_order(f, prefer))
    done = 0
    fresh_after = timezone.now() - timedelta(days=SHARE_FULL_REWALK_DAYS)
    for unit in units:
        if time.time() - t0 > budget_seconds:
            break
        prefix = unit.path
        sub_folders = {f.item_id: f for f in Folder.objects.filter(repo=repo, path__startswith=prefix + "/")}
        sub_folders[share_client.item_key(prefix)] = unit
        if unit.walked_at and unit.walked_at >= fresh_after and unit.id not in touched_set:
            try:
                dirs = {share_client.item_key(d["rel"]): d["mtime"] for d in share_client.walk(share_root, on_error=on_error, start_rel=prefix, dirs_only=True)}
            except share_client.ShareUnavailable as e:
                errors.append((prefix, str(e)[:200]))
                continue
            known = {k: f.mtime for k, f in sub_folders.items() if k != share_client.item_key(prefix) and not f.is_deleted}
            if dirs.keys() == known.keys() and all(known[k] and abs((dirs[k] - known[k]).total_seconds()) < 1 for k in dirs):
                stats["units_verified"] += 1
                done += 1
                continue
        sub_files = {r["item_id"]: r for r in fetch_dict("SELECT id, item_id, etag, is_deleted, folder_id FROM documents_file WHERE repo_id = %s AND path LIKE %s", [repo.id, _like_prefix(prefix)])}
        try:
            entries = list(share_client.walk(share_root, on_error=on_error, start_rel=prefix))
        except share_client.ShareUnavailable as e:
            errors.append((prefix, str(e)[:200]))
            continue
        with transaction.atomic():
            seen_f, seen_x = _share_upsert(repo, run, entries, sub_folders, sub_files, stats, touched_folders, touched_files, now)
            _mark_gone(repo, sub_folders, sub_files, seen_f, seen_x, {share_client.item_key(prefix)}, stats)
            Folder.objects.filter(pk=unit.pk).update(walked_at=timezone.now())
        done += 1
        stats["units_walked"] += 1
    pending = len(units) - done
    repo.last_indexed = now
    repo.last_error = ("walk incomplete: %d of %d job folders this run (budget %ds) — resumes next run" % (done, len(units), budget_seconds) if pending else "") \
        + ((" · %d unreadable folders (first: %s)" % (len(errors), errors[0][0])) if errors else "")
    repo.file_count = File.objects.filter(repo=repo, is_deleted=False).count()
    repo.folder_count = Folder.objects.filter(repo=repo, is_deleted=False).count()
    repo.save()
    stats["pending_units"] = pending
    stats["seconds"] = round(time.time() - t0, 1)
    return dict(stats, errors=len(errors), touched_folders=touched_folders, touched_files=touched_files)


# ------------------------------------------------------------------------------------------- linking
class _Known:
    """The SL numbers, unique SL proposal numbers and bid vocabulary the rules resolve against (built once per run)."""

    def __init__(self):
        rows = fetch_dict("SELECT id, canonical_project_number cpn, quote_reference, customer_id FROM core_project")
        self.project_id = {r["cpn"]: r["id"] for r in rows}
        self.known = set(self.project_id)
        by_ref = defaultdict(list)
        for r in rows:
            k = linking.normalize_quote_reference(r["quote_reference"])
            if k:
                by_ref[k].append(r["id"])
        self.unique_ref = {k: v[0] for k, v in by_ref.items() if len(v) == 1}
        bids = fetch_dict("SELECT id, client_name, project_name, project_id FROM bids_bid WHERE project_name <> ''")
        # the P: drive names job folders by the Project List's Project ID ('9955 - NEIU EL Centro WAP Install'); the id is
        # unique per list row, and the bid carries the SL job once awarded
        self.portal_bid = {r["pid"].lstrip("0") or r["pid"]: (r["id"], r["project_id"])
                           for r in fetch_dict("SELECT id, portal_project_id pid, project_id FROM bids_bid WHERE source = 'list' AND portal_project_id ~ '^[0-9]{3,5}$'")}
        self.bid_words = {}
        self.word_index = defaultdict(set)
        for b in bids:
            ws = set(linking.words("%s %s" % (b["client_name"] or "", b["project_name"] or "")))
            if ws:
                self.bid_words[b["id"]] = (ws, b["client_name"], b["project_name"])
                for w in ws:
                    self.word_index[w].add(b["id"])

    def bid_candidates(self, name):
        """Bids sharing at least two words (or one long one) with the name — the fuzzy rule only scores these."""
        ws = set(linking.words(name))
        if not ws:
            return []
        counts = Counter()
        for w in ws:
            for bid_id in self.word_index.get(w, ()):
                counts[bid_id] += 1
        out = []
        for bid_id, n in counts.items():
            if n >= 2:
                _, client, pname = self.bid_words[bid_id]
                out.append((bid_id, client, pname))
        return out


def _links_for_folder(folder, K):
    out = []
    rules = linking.folder_rules(folder.name)
    plan = linking.planner_plan_name(folder.path + "/")
    if plan and folder.name.rsplit("_", 1)[0] == plan:
        rules += [(c, "planner_folder", linking.RULES["planner_folder"][1], dict(ev, plan=plan)) for c, _, _, ev in linking.folder_rules(plan)]
    for cands, rule, conf, ev in rules:
        cpn = linking.resolve(cands, K.known)
        if cpn:
            out.append(("project", K.project_id[cpn], rule, conf, dict(ev, number=cpn)))
    for ref in linking.sl_quote_refs(folder.name):
        if ref in K.unique_ref:
            out.append(("project", K.unique_ref[ref], "sl_quote_reference", linking.RULES["sl_quote_reference"][1], {"matched": ref, "text": folder.name}))
    pid = linking.portal_id_hit(folder.name)
    if pid and pid in K.portal_bid:
        bid_id, project_id = K.portal_bid[pid]
        conf = linking.RULES["portal_id"][1]
        out.append(("bid", bid_id, "portal_id", conf, {"matched": pid, "text": folder.name}))
        if project_id and not any(t == "project" and i == project_id for t, i, *_ in out):
            out.append(("project", project_id, "portal_id", conf, {"matched": pid, "text": folder.name, "via": "bid %d" % bid_id}))
    # a client folder under '<year> Projects' holds every job of that client: it must not fuzzy-link to one bid
    is_client_folder = folder.depth == 2 and bool(_YEAR_TOP.match(folder.path))
    if not out and folder.depth <= 3 and not is_client_folder:
        for bid_id, score in linking.bid_name_matches(folder.name, K.bid_candidates(folder.name)):
            out.append(("bid", bid_id, "bid_name", min(linking.RULES["bid_name"][1] + (score - 0.6) / 2, 0.7), {"text": folder.name, "similarity": score}))
    return out


def _links_for_file(f, K):
    out = []
    for cands, rule, conf, ev in linking.file_rules(f.name):
        cpn = linking.resolve(cands, K.known)
        if cpn:
            out.append(("project", K.project_id[cpn], rule, conf, dict(ev, number=cpn)))
    for ref in linking.sl_quote_refs(f.name):
        if ref in K.unique_ref:
            out.append(("project", K.unique_ref[ref], "sl_quote_reference", linking.RULES["sl_quote_reference"][1], {"matched": ref, "text": f.name}))
    return out


def link_all(run, folder_ids=None, file_ids=None, full=False):
    """Apply the rules to the given folders / files (every live one when full). Confirmed and rejected links are kept
    as they are; automatic links that no longer fire are removed. Then the effective link is written on every file."""
    K = _Known()
    stats = Counter()
    folders = Folder.objects.filter(is_deleted=False)
    files = File.objects.filter(is_deleted=False)
    if not full:
        folders = folders.filter(id__in=folder_ids or [])
        files = files.filter(id__in=file_ids or [])
    with transaction.atomic():
        for kind, qs in (("folder", folders), ("file", files)):
            ids = list(qs.values_list("id", flat=True))
            for i in range(0, len(ids), 2000):
                chunk = list(qs.filter(id__in=ids[i:i + 2000]))
                subj_field = "folder_id" if kind == "folder" else "file_id"
                current = defaultdict(dict)
                for l in DocLink.objects.filter(**{subj_field + "__in": [o.id for o in chunk]}):
                    current[getattr(l, subj_field)][l.dedupe] = l
                for obj in chunk:
                    proposed = _links_for_folder(obj, K) if kind == "folder" else _links_for_file(obj, K)
                    keep = set()
                    for target, tid, rule, conf, ev in proposed:
                        pid, bid, cid = (tid if target == "project" else None), (tid if target == "bid" else None), (tid if target == "customer" else None)
                        key = DocLink.dedupe_key(obj.id if kind == "file" else None, obj.id if kind == "folder" else None, pid, bid, cid)
                        if key in keep:          # the same target proposed twice by two rules: the first (strongest) wins
                            continue
                        keep.add(key)
                        l = current[obj.id].get(key)
                        if l is None:
                            l = DocLink.objects.create(file=obj if kind == "file" else None, folder=obj if kind == "folder" else None, project_id=pid, bid_id=bid,
                                                       customer_id=cid, rule=rule, confidence=conf, evidence=ev, dedupe=key)
                            current[obj.id][key] = l
                            stats["links_new"] += 1
                        elif l.state == DocLink.State.AUTO and (l.rule != rule or float(l.confidence) != conf):
                            l.rule, l.confidence, l.evidence = rule, conf, ev
                            l.save(update_fields=["rule", "confidence", "evidence", "updated_at"])
                            stats["links_updated"] += 1
                    for key, l in current[obj.id].items():
                        if key not in keep and l.state == DocLink.State.AUTO and l.rule != "manual":
                            l.delete(); stats["links_removed"] += 1
    stats.update(apply_effective_links(file_ids=None if full else file_ids, folder_ids=None if full else folder_ids))
    return dict(stats)


def _best(links):
    """Best usable link among candidates: confirmed first, then confidence, then the nearest subject (own name
    before the parent folder before the grandparent); rejected never. Each candidate carries a `dist`."""
    usable = [l for l in links if l["state"] != "rejected"]
    if not usable:
        return None
    usable.sort(key=lambda l: (l["state"] != "confirmed", -float(l["confidence"]), l.get("dist", 0)))
    return usable[0]


def apply_effective_links(file_ids=None, folder_ids=None):
    """File.linked_* = the best link among the file's own links and every ancestor folder's (a folder named with the
    job number beats a weaker number inside the file name; a confirmed link beats everything). Files under the given
    folders (any depth) and the given files are recomputed; None = everything."""
    folder_rows = fetch_dict("SELECT id, parent_id, repo_id, path FROM documents_folder WHERE NOT is_deleted")
    parent = {r["id"]: r["parent_id"] for r in folder_rows}
    fl = defaultdict(list)
    for l in fetch_dict("SELECT folder_id, project_id, bid_id, customer_id, rule, confidence, state FROM documents_doclink WHERE folder_id IS NOT NULL"):
        fl[l["folder_id"]].append(l)
    chain_cache = {}

    def chain_links(fid):
        """Every ancestor folder's links with their distance (1 = the file's folder)."""
        if fid in chain_cache:
            return chain_cache[fid]
        out, cur, dist, seen = [], fid, 1, set()
        while cur and cur not in seen and dist < 64:
            seen.add(cur)
            out += [dict(l, dist=dist) for l in fl.get(cur, [])]
            cur, dist = parent.get(cur), dist + 1
        chain_cache[fid] = out
        return out

    where, params = "NOT f.is_deleted", []
    if file_ids is not None or folder_ids is not None:
        under = set()
        if folder_ids:
            children = defaultdict(list)
            for r in folder_rows:
                children[r["parent_id"]].append(r["id"])
            stack = list(folder_ids)
            while stack:
                x = stack.pop()
                if x in under:
                    continue
                under.add(x)
                stack.extend(children.get(x, []))
        where = "NOT f.is_deleted AND (f.id = ANY(%s) OR f.folder_id = ANY(%s))"
        params = [list(file_ids or []), list(under)]
    files = fetch_dict("SELECT f.id, f.folder_id, f.linked_project_id, f.linked_bid_id, f.linked_customer_id, f.link_rule, f.link_confidence, f.link_via FROM documents_file f WHERE %s" % where, params)
    own = defaultdict(list)
    ids = [r["id"] for r in files]
    for i in range(0, len(ids), 5000):
        for l in fetch_dict("SELECT file_id, project_id, bid_id, customer_id, rule, confidence, state FROM documents_doclink WHERE file_id = ANY(%s)", [ids[i:i + 5000]]):
            own[l["file_id"]].append(dict(l, dist=0))
    updates = []
    for r in files:
        cands = own.get(r["id"], []) + (chain_links(r["folder_id"]) if r["folder_id"] else [])
        b = _best(cands)
        via = ("file" if b["dist"] == 0 else "folder") if b else ""
        new = (b["project_id"] if b else None, b["bid_id"] if b else None, b["customer_id"] if b else None, b["rule"] if b else "", float(b["confidence"]) if b else None, via)
        old = (r["linked_project_id"], r["linked_bid_id"], r["linked_customer_id"], r["link_rule"], float(r["link_confidence"]) if r["link_confidence"] is not None else None, r["link_via"])
        if new != old:
            updates.append((r["id"],) + new)
    from django.db import connection
    from psycopg2.extras import execute_values
    if updates:
        with connection.cursor() as cur:
            execute_values(cur.cursor, """UPDATE documents_file AS f SET linked_project_id = v.p, linked_bid_id = v.b, linked_customer_id = v.c, link_rule = v.r,
                                          link_confidence = v.conf, link_via = v.via FROM (VALUES %s) AS v(id, p, b, c, r, conf, via) WHERE f.id = v.id""",
                           updates, template="(%s, %s::bigint, %s::bigint, %s::bigint, %s, %s::numeric, %s)", page_size=2000)
    return {"effective_updated": len(updates), "effective_checked": len(files)}


# ------------------------------------------------------------------------------------------- content + text
def content_bytes(f, max_bytes=None):
    """The file's bytes through the read-only clients (Graph /content or the share). None when over the cap."""
    cap = max_bytes or share_client.max_text_bytes()
    if f.repo.is_share:
        return share_client.read_bytes(f.path, cap, override=f.repo.root_path or None)
    if f.size and f.size > cap:
        return None
    return g.content("/drives/%s/items/%s/content" % (f.repo.drive_id, f.item_id))


def extract_texts(run, limit=EXTRACT_LIMIT, budget_seconds=EXTRACT_BUDGET_S):
    """Bounded per run: new / changed files first (proposal-looking names, then linked files, newest first)."""
    t0 = time.time()
    stats = Counter()
    qs = File.objects.filter(is_deleted=False, text_status=File.TextStatus.NONE, ext__in=sorted(extract.TEXT_EXTS)).select_related("repo")
    ordered = sorted(qs.order_by("-mtime")[:limit * 4], key=lambda f: (not any(w in f.name.lower() for w in PROPOSAL_NAME_WORDS), f.linked_project_id is None and f.linked_bid_id is None))
    for f in ordered[:limit]:
        if time.time() - t0 > budget_seconds:
            stats["budget_stop"] = 1
            break
        if f.size > share_client.max_text_bytes():
            f.text_status, f.text_error = File.TextStatus.SKIPPED, "over size cap"; f.save(update_fields=["text_status", "text_error"])
            stats["skipped"] += 1
            continue
        try:
            data = content_bytes(f)
        except Exception as e:  # noqa
            f.text_status, f.text_error = File.TextStatus.FAILED, ("fetch: %s" % e)[:300]; f.save(update_fields=["text_status", "text_error"])
            stats["failed"] += 1
            continue
        if data is None:
            f.text_status, f.text_error = File.TextStatus.SKIPPED, "over size cap"; f.save(update_fields=["text_status", "text_error"])
            stats["skipped"] += 1
            continue
        r = extract.extract(data, f.ext, f.name)
        if r["ok"]:
            DocText.objects.update_or_create(file=f, defaults={"extractor": r["extractor"], "pages": r["pages"], "page_offsets": r["page_offsets"],
                                                                "text": r["text"], "chars": len(r["text"]), "truncated": r["truncated"]})
            f.text_status, f.text_error = File.TextStatus.OK, ""
            stats["ok"] += 1
        else:
            f.text_status, f.text_error = File.TextStatus.FAILED, r["error"][:300]
            stats["failed"] += 1
        f.save(update_fields=["text_status", "text_error"])
    stats["seconds"] = round(time.time() - t0, 1)
    return dict(stats)


# ------------------------------------------------------------------------------------------- proposal checks
def standard_clauses():
    p = Path(settings.BASE_DIR) / "docs" / "proposal_standard_clauses.md"
    try:
        return checks.load_standard_clauses(p.read_text())
    except OSError:
        return {}


def _bid_for(f):
    """The bid a proposal is checked against: the file's bid link, else the newest bid on the linked project."""
    if f.linked_bid_id:
        return f.linked_bid
    if f.linked_project_id:
        return Bid.objects.filter(project_id=f.linked_project_id).order_by("-portal_modified", "-id").first()
    return None


def run_proposal_checks(run, limit=CHECK_LIMIT):
    """Findings for every extracted proposal-looking document whose version has not been checked yet."""
    std = standard_clauses()
    stats = Counter()
    qs = File.objects.filter(is_deleted=False, text_status=File.TextStatus.OK, ext__in=("pdf", "docx", "txt", "msg")).exclude(checked_etag=models_f("etag")).select_related("repo", "linked_bid", "linked_project")
    name_q = Q()
    for w in PROPOSAL_NAME_WORDS:
        name_q |= Q(name__icontains=w)
    qs = qs.filter(name_q | Q(linked_bid__isnull=False)).order_by("-mtime")[:limit]
    for f in qs:
        try:
            dt = f.text
        except DocText.DoesNotExist:
            continue
        bid = _bid_for(f)
        bd = {"project_name": bid.project_name, "client_name": bid.client_name, "value": bid.value, "bid_due": bid.bid_due, "submitted_on": bid.submitted_on} if bid else None
        found = checks.run_checks(dt.text, bid=bd, standard=std, name=f.name, page_offsets=dt.page_offsets)
        fps = set()
        with transaction.atomic():
            for x in found:
                fp = content_hash(x["check_id"], x["message"], sorted(x["evidence"].items()))[:64]
                fps.add(fp)
                obj, created = DocFinding.objects.get_or_create(file=f, fingerprint=fp, defaults={
                    "bid": bid, "project": f.linked_project if f.linked_project_id else (bid.project if bid and bid.project_id else None),
                    "check_id": x["check_id"], "severity": x["severity"], "message": x["message"], "evidence": x["evidence"]})
                stats["new" if created else "seen"] += 1
                if not created and obj.status == DocFinding.Status.FIXED:
                    obj.status = DocFinding.Status.OPEN; obj.save(update_fields=["status", "updated_at"])
            stale = DocFinding.objects.filter(file=f, status=DocFinding.Status.OPEN).exclude(fingerprint__in=fps)
            stats["fixed"] += stale.update(status=DocFinding.Status.FIXED)
            f.checked_etag = f.etag; f.save(update_fields=["checked_etag"])
        stats["files"] += 1
    return dict(stats)


def models_f(name):
    from django.db.models import F
    return F(name)


# ------------------------------------------------------------------------------------------- list attachments
def load_list_attachments(run):
    """Placeholder rows: Graph exposes only the Attachments flag on a Project List row (the names need the SharePoint
    API + certificate credential, docs/11). One row per flagged bid, readable=False, until that lands."""
    flagged = list(Bid.objects.filter(attachments_flag=True).values_list("id", flat=True))
    have = set(ListAttachment.objects.filter(bid_id__in=flagged, name="").values_list("bid_id", flat=True))
    ListAttachment.objects.bulk_create([ListAttachment(bid_id=b, name="", readable=False) for b in flagged if b not in have])
    return {"bids_with_attachments": len(flagged), "placeholders_new": len(flagged) - len(have)}


# ------------------------------------------------------------------------------------------- data quality
def check_quality(run, share_root=None):
    stats = Counter()
    h = share_client.health(share_root)
    if not h["ok"]:
        issue(run, "doc_share_unreadable", "warning", source_system="share", source_key="mount", root=h["root"], error=h["error"],
              fix="Grant the terminal / launchd python access to Network Volumes (System Settings › Privacy & Security), or mount the share (scripts/mount_share.sh)")
        stats["share_unreadable"] = 1
    for r in Repo.objects.filter(enabled=True).exclude(last_error=""):
        issue(run, "doc_repo_error", "warning", source_system="sharepoint" if not r.is_share else "share", source_key=r.key, repo=r.name, error=r.last_error[:300])
        stats["repo_errors"] += 1
    n_failed = File.objects.filter(is_deleted=False, text_status=File.TextStatus.FAILED).count()
    if n_failed:
        issue(run, "doc_extract_failed", "info", source_system="local", source_key="summary", files=n_failed, fix="Documents page › extraction filter: scanned PDFs need OCR (not installed), password-protected files stay unreadable")
        stats["extract_failed"] = n_failed
    K_known = set(Project.objects.values_list("canonical_project_number", flat=True))
    n = 0
    for fo in Folder.objects.filter(is_deleted=False, depth__lte=3).select_related("repo").iterator():
        hits = linking.number_hits(fo.name)
        if not hits:
            continue
        if any(linking.resolve(c, K_known) for c, _, _, _ in hits):
            continue
        if fo.links.filter(state=DocLink.State.CONFIRMED).exists():
            continue
        n += 1
        if n <= 300:
            issue(run, "doc_folder_no_job", "info", source_system="share" if fo.repo.is_share else "sharepoint", source_key="%s:%s" % (fo.repo.key, fo.path[:200]),
                  folder=fo.name, repo=fo.repo.name, number=hits[0][1], fix="Rename the folder to the SL job number, or link it by hand on the Documents page")
    stats["folder_no_job"] = n
    linked = set(File.objects.filter(is_deleted=False, linked_project__isnull=False).values_list("linked_project_id", flat=True))
    open_jobs = list(Project.objects.filter(lifecycle_state__in=OPEN_STATES, is_internal_bucket=False, is_template_or_void=False).values_list("id", "canonical_project_number"))
    m = 0
    for pid, cpn in open_jobs:
        if pid not in linked:
            m += 1
            issue(run, "doc_job_no_folder", "info", project_id=pid, source_system="local", source_key=cpn, fix="No SharePoint or P: drive file names this job yet")
    stats["job_no_folder"] = m
    return dict(stats)


# ------------------------------------------------------------------------------------------- orchestration
def refresh_all_step(run, full=False, extract_limit=EXTRACT_LIMIT, check_limit=CHECK_LIMIT, share_root=None, do_extract=True, do_checks=True, share_budget=SHARE_BUDGET_S):
    """The refresh_all step: index every enabled repo (each best-effort), link what changed, extract + check bounded,
    attachments placeholders, Data Quality. Returns a stats dict for run.steps."""
    out = {}
    g.permissions_audit()
    repos = ensure_repos(share_root=share_root)
    touched_folders, touched_files = [], []
    for repo in repos:
        if not repo.enabled:
            continue
        try:
            if repo.is_share:
                r = walk_share(repo, run, share_root=share_root, budget_seconds=share_budget)
            else:
                r = index_library(repo, run, full=full)
            touched_folders += r.pop("touched_folders", [])
            touched_files += r.pop("touched_files", [])
            out[repo.key] = r
        except Exception as e:  # noqa - one library's outage must not sink the step
            log.exception("document index failed for %s", repo.key)
            repo.last_error = str(e)[:500]; repo.save(update_fields=["last_error"])
            out[repo.key] = {"error": str(e)[:200]}
    out["link"] = link_all(run, folder_ids=touched_folders, file_ids=touched_files, full=full)
    if do_extract:
        out["extract"] = extract_texts(run, limit=extract_limit)
    if do_checks:
        out["checks"] = run_proposal_checks(run, limit=check_limit)
    out["attachments"] = load_list_attachments(run)
    out["quality"] = check_quality(run, share_root=share_root)
    return out


def recent_changes(days=14, limit=200, project_id=None):
    qs = File.objects.filter(is_deleted=False, mtime__gte=timezone.now() - timedelta(days=days)).select_related("repo", "linked_project").order_by("-mtime")
    if project_id:
        qs = qs.filter(linked_project_id=project_id)
    return list(qs[:limit])
