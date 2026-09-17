"""Microsoft Graph client for SharePoint lists / drives and Planner — read-only (SharePoint spec §2, §13).

Client-credentials app "Pace Secure AI SharePoint Reader" (`.env`: PACE_SHAREPOINT_READER_APPLICATION_CLIENT_ID,
PACE_SHAREPOINT_READER_DIRECTORY_TENANT_ID, PACE_SHAREPOINT_READER_CLIENT_SECRET_VALUE). Only GET is ever issued —
`request()` raises on any other verb — and `permissions_audit()` refuses to run a refresh when the token carries a
write role. Every response body is archived under APP_SUPPORT_DIR/graph_raw/<date>/ (gzip) like the CNET client.

Paging follows `@odata.nextLink`; `delta()` follows `@odata.deltaLink`; 429 / 503 back off with Retry-After.
"""

import base64
import gzip
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import requests
from django.conf import settings

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
HOSTNAME = "pacesystems0.sharepoint.com"
WRITE_ROLE = re.compile(r"(ReadWrite|Write|Manage|Send|Create|Delete|FullControl)", re.I)


class GraphError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


class WriteAttemptError(Exception):
    """Raised before any non-GET request could leave the process."""


def _creds():
    e = settings.PACE_SHAREPOINT_READER
    missing = [k for k, v in e.items() if not v]
    if missing:
        raise GraphError("SharePoint reader credentials missing in .env: %s" % ", ".join(missing))
    return e


_token = {"value": None, "expires": 0}


def token(force=False):
    if not force and _token["value"] and _token["expires"] > time.time() + 120:
        return _token["value"]
    c = _creds()
    r = requests.post("https://login.microsoftonline.com/%s/oauth2/v2.0/token" % c["tenant_id"],
                      data={"client_id": c["client_id"], "client_secret": c["client_secret"], "grant_type": "client_credentials", "scope": SCOPE},
                      timeout=30)
    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = {}
        raise GraphError("token request failed: HTTP %s %s %s" % (r.status_code, body.get("error"), (body.get("error_description") or "")[:200]), r.status_code)
    j = r.json()
    _token["value"], _token["expires"] = j["access_token"], time.time() + int(j.get("expires_in", 3600))
    return _token["value"]


def token_claims():
    """Decoded (unverified) claims of the current token — used for the permissions audit only."""
    t = token()
    payload = t.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def permissions_audit():
    """Prove the app is read-only: every role must be a read role. Returns the role list (recorded on the run)."""
    roles = token_claims().get("roles") or []
    bad = [r for r in roles if WRITE_ROLE.search(r)]
    if bad:
        raise WriteAttemptError("Graph app carries write roles %s — refusing to run" % bad)
    return {"app": token_claims().get("app_displayname"), "roles": roles}


# ------------------------------------------------------------------------------------------- archive
def _archive_dir():
    d = Path(settings.APP_SUPPORT_DIR) / "graph_raw" / date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive(label, payload):
    """gzip a JSON payload under today's archive folder; returns the path (never raises into the loader)."""
    try:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)[:120]
        p = _archive_dir() / ("%s_%s.json.gz" % (safe, time.strftime("%H%M%S")))
        with gzip.open(p, "wt", encoding="utf8") as f:
            json.dump(payload, f, default=str)
        return str(p)
    except Exception as e:  # noqa
        log.warning("graph archive failed for %s: %s", label, e)
        return None


# ------------------------------------------------------------------------------------------- requests
def request(url, params=None, headers=None, stream=False, retries=6):
    """GET only. Absolute URL or a path under /v1.0. Retries 429 / 503 / 504 with Retry-After or exponential wait."""
    if url.startswith("/"):
        url = GRAPH + url
    if not url.startswith("https://graph.microsoft.com/"):
        raise GraphError("refusing non-Graph URL %s" % url[:80])
    h = {"Authorization": "Bearer " + token(), "Accept": "application/json"}
    if headers:
        h.update(headers)
    wait = 2.0
    for attempt in range(retries):
        r = requests.get(url, params=params, headers=h, timeout=120, stream=stream)
        if r.status_code in (429, 503, 504):
            ra = r.headers.get("Retry-After")
            delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else wait
            log.warning("graph %s -> %s, waiting %.0fs", url[:90], r.status_code, delay)
            time.sleep(min(delay, 120))
            wait = min(wait * 2, 60)
            continue
        if r.status_code == 401 and attempt == 0:
            h["Authorization"] = "Bearer " + token(force=True)
            continue
        return r
    raise GraphError("graph request gave up after %d attempts: %s" % (retries, url[:120]), r.status_code)


def get(url, params=None, headers=None):
    r = request(url, params=params, headers=headers)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except Exception:
            err = {}
        raise GraphError("HTTP %s %s: %s (%s)" % (r.status_code, err.get("code"), (err.get("message") or "")[:200], url[:120]), r.status_code, err.get("code"))
    return r.json()


def get_optional(url, params=None):
    """None on 404 / 403 instead of raising (a plan a group no longer has, a deleted item)."""
    try:
        return get(url, params=params)
    except GraphError as e:
        if e.status in (403, 404):
            return None
        raise


def paged(url, params=None, label=None, max_pages=10000):
    """Every `value` item across @odata.nextLink pages (archives each page when a label is given)."""
    out, pages = [], 0
    while url and pages < max_pages:
        j = get(url, params=params)
        params = None
        pages += 1
        if label:
            archive("%s_p%d" % (label, pages), j)
        out.extend(j.get("value", []))
        url = j.get("@odata.nextLink")
    return out


def delta(url, params=None, label=None):
    """Follow a delta query to the end. Returns (items, deltaLink). Start with the /delta URL or a stored deltaLink."""
    out, link, pages = [], None, 0
    while url:
        j = get(url, params=params)
        params = None
        pages += 1
        if label:
            archive("%s_d%d" % (label, pages), j)
        out.extend(j.get("value", []))
        link = j.get("@odata.deltaLink")
        url = j.get("@odata.nextLink")
    return out, link


def content(url):
    """Raw bytes of a drive item's /content (streamed)."""
    r = request(url, stream=True)
    if r.status_code >= 400:
        raise GraphError("HTTP %s fetching content %s" % (r.status_code, url[:120]), r.status_code)
    return r.content


# ------------------------------------------------------------------------------------------- SharePoint helpers
_site_cache = {}


def site(path="/"):
    """Site object by server-relative path ('/sites/ProjectPortal', '/' for the root)."""
    if path not in _site_cache:
        _site_cache[path] = get("/sites/%s:%s" % (HOSTNAME, path) if path != "/" else "/sites/root")
    return _site_cache[path]


def lists(site_id):
    return paged("/sites/%s/lists" % site_id, {"$top": 200})


def list_by_name(site_id, display_name):
    for l in lists(site_id):
        if l.get("displayName") == display_name:
            return l
    raise GraphError("list %r not found on site %s" % (display_name, site_id))


def list_columns(site_id, list_id):
    return paged("/sites/%s/lists/%s/columns" % (site_id, list_id))


def list_items(site_id, list_id, select_fields=None, top=500, label=None):
    expand = "fields" if not select_fields else "fields($select=%s)" % ",".join(select_fields)
    return paged("/sites/%s/lists/%s/items" % (site_id, list_id), {"expand": expand, "$top": top}, label=label)


def item_versions(site_id, list_id, item_id, select_fields=None):
    expand = "fields" if not select_fields else "fields($select=%s)" % ",".join(select_fields)
    return paged("/sites/%s/lists/%s/items/%s/versions" % (site_id, list_id, item_id), {"expand": expand, "$top": 200})


def drives(site_id):
    return paged("/sites/%s/drives" % site_id)


def drive_delta(drive_id, delta_link=None, label=None):
    url = delta_link or ("/drives/%s/root/delta" % drive_id)
    return delta(url, {"$select": "id,name,file,folder,size,parentReference,webUrl,lastModifiedDateTime,createdDateTime,lastModifiedBy,eTag,deleted"} if not delta_link else None, label=label)


def drive_children(drive_id, item_id=None, top=500):
    url = "/drives/%s/%s/children" % (drive_id, "root" if item_id is None else "items/%s" % item_id)
    return paged(url, {"$top": top, "$select": "id,name,file,folder,size,parentReference,webUrl,lastModifiedDateTime,createdDateTime,lastModifiedBy,eTag"})


# ------------------------------------------------------------------------------------------- Planner helpers
def unified_groups():
    return paged("/groups", {"$top": 999, "$select": "id,displayName,mail,visibility,createdDateTime,description", "$filter": "groupTypes/any(c:c eq 'Unified')"})


def group_plans(group_id):
    j = get_optional("/groups/%s/planner/plans" % group_id, {"$select": "id,title,createdDateTime,owner"})
    return (j or {}).get("value", [])


def plan_buckets(plan_id):
    return paged("/planner/plans/%s/buckets" % plan_id)


def plan_tasks(plan_id):
    return paged("/planner/plans/%s/tasks" % plan_id)


def plan_details(plan_id):
    return get_optional("/planner/plans/%s/details" % plan_id)


def task_details(task_id):
    return get_optional("/planner/tasks/%s/details" % task_id)


def users_basic():
    return paged("/users", {"$top": 999, "$select": "id,displayName,mail,userPrincipalName,jobTitle,department,accountEnabled"})
