"""Loaders for the bid pipeline (SharePoint spec §2, §3, §4.1–4.2, §5.1, §12): Project List + Archive -> Bid,
version history -> BidVersion, SL linking, bidder / rep / client aliases, division, the estimator written onto the SL
project, the daily BidSnapshot and the Data Quality issues. Read-only against SharePoint; writes only PCA tables.
"""

import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.models import Customer, Division, Employee, Project
from apps.ingestion.bulk import content_hash, fetch_dict
from apps.ingestion.loaders import issue
from apps.ingestion.sources import graph_client as g

from . import rules
from .models import Bid, BidClientAlias, BidderAlias, BidSnapshot, BidVersion

log = logging.getLogger(__name__)

LIST_NAME, ARCHIVE_NAME, CLIENTS_NAME = "Project List", "Project Archive", "Client List"
LIST_FIELDS = ["Title", "SalesOrder_x0023_", "ClientLookupId", "ProjectName", "BidDueDate", "Status", "StartDate", "EndDate", "Budget",
               "ProjectValue", "SalesRep", "Bidder", "Probability_x0020_of_x0020_Close", "PONumber", "DateSubmitted", "DateAwarded",
               "Increment_x0020_Number", "Project_x0020_ID", "COMMENTS", "_x0025_OFCLOSE", "WalkthroughDate", "WalkthroughPersonnel",
               "BOMStatus", "BallInCourt", "ProjectManagerv2", "Attachments"]
VERSION_FIELDS = ["Status", "ProjectValue", "Budget", "Bidder", "Probability_x0020_of_x0020_Close", "Title"]
ARCHIVE_MAP = {"Title": "portal_project_id", "field_1": "client_name", "field_2": "project_name", "field_3": "job_number_raw",
               "field_4": "sales_order", "field_5": "bid_due", "field_6": "status_raw", "field_7": "start", "field_8": "end",
               "field_9": "budget", "field_10": "value", "field_11": "sales_rep_raw", "field_12": "bidder_raw",
               "field_13": "pm_raw", "field_14": "probability_raw", "field_15": "po_number", "field_16": "submitted_on", "field_17": "awarded_on"}
BID_COLS = ["pm_raw", "portal_project_id", "job_number_raw", "client_name", "project_name", "stage", "stage_flag", "status_raw", "bid_due", "submitted_on",
            "awarded_on", "start", "end", "budget", "value", "bidder_raw", "sales_rep_raw", "house_account", "probability", "pct_close_raw",
            "ball_in_court", "bom_status", "walkthrough_date", "walkthrough_raw", "po_number", "sales_order", "comments", "attachments_flag",
            "created_by_raw", "portal_created", "portal_modified", "web_url"]
_MAXLEN = {f.name: f.max_length for f in Bid._meta.get_fields() if getattr(f, "max_length", None)}


def _s(v, n=None):
    s = "" if v is None else str(v).strip()
    return s[:n] if n else s


def _clamp(d):
    for k, v in list(d.items()):
        if isinstance(v, str) and k in _MAXLEN and len(v) > _MAXLEN[k]:
            d[k] = v[:_MAXLEN[k]]
    return d


def _portal():
    site = g.site(settings.SHAREPOINT_PROJECT_PORTAL_SITE)
    return site["id"]


# ------------------------------------------------------------------------------------------- clients
def load_clients(run):
    """Client List (lookup target) -> {item id: name}; kept in memory for the list load and used to seed BidClientAlias."""
    sid = _portal()
    lst = g.list_by_name(sid, CLIENTS_NAME)
    items = g.list_items(sid, lst["id"], ["Title"], label="client_list")
    names = {str(it["id"]): _s((it.get("fields") or {}).get("Title"), 255) for it in items}
    return names


# ------------------------------------------------------------------------------------------- list + archive
def _row_from_list(it, clients):
    f = it.get("fields") or {}
    stage, flag = rules.normalize_status(f.get("Status"))
    created_by = ((it.get("createdBy") or {}).get("user") or {}).get("displayName") or ""
    d = {
        "portal_project_id": _s(rules.portal_id_text(f.get("Project_x0020_ID")), 32),
        "job_number_raw": _s(f.get("Title"), 64),
        "client_name": clients.get(str(f.get("ClientLookupId") or ""), ""),
        "project_name": _s(f.get("ProjectName"), 255),
        "stage": stage, "stage_flag": flag, "status_raw": _s(rules.clean_choice(f.get("Status")), 64),
        "bid_due": rules.to_date(f.get("BidDueDate")), "submitted_on": rules.to_date(f.get("DateSubmitted")),
        "awarded_on": rules.to_date(f.get("DateAwarded")), "start": rules.to_date(f.get("StartDate")), "end": rules.to_date(f.get("EndDate")),
        "budget": rules.to_decimal(f.get("Budget")), "value": rules.to_decimal(f.get("ProjectValue")),
        "bidder_raw": _s(rules.clean_choice(f.get("Bidder")), 64), "sales_rep_raw": _s(rules.clean_choice(f.get("SalesRep")), 64),
        "house_account": rules.is_house_rep(f.get("SalesRep")),
        "probability": rules.probability_from(f.get("Probability_x0020_of_x0020_Close")),
        "pct_close_raw": _s(f.get("_x0025_OFCLOSE"), 24),
        "ball_in_court": _s(rules.clean_choice(f.get("BallInCourt")), 64), "bom_status": _s(rules.clean_choice(f.get("BOMStatus")), 32),
        "walkthrough_date": rules.to_date(f.get("WalkthroughDate")), "walkthrough_raw": _s(rules.clean_choice(f.get("WalkthroughPersonnel")), 64),
        "po_number": _s(f.get("PONumber"), 64), "sales_order": _s(f.get("SalesOrder_x0023_"), 32),
        "comments": _s(f.get("COMMENTS")), "attachments_flag": bool(f.get("Attachments")),
        "created_by_raw": _s(created_by, 128),
        "portal_created": parse_datetime(it.get("createdDateTime")) if it.get("createdDateTime") else None,
        "portal_modified": parse_datetime(it.get("lastModifiedDateTime")) if it.get("lastModifiedDateTime") else None,
        "web_url": _s(it.get("webUrl"), 500),
    }
    d["pm_raw"] = _s(f.get("ProjectManagerv2"), 64)
    return _clamp(d)


def _row_from_archive(it):
    f = it.get("fields") or {}
    d = {k: None for k in BID_COLS}
    for src, dst in ARCHIVE_MAP.items():
        d[dst] = f.get(src)
    stage, flag = rules.archive_stage(*rules.normalize_status(d.get("status_raw")))
    out = {
        "portal_project_id": _s(d.get("portal_project_id"), 32), "job_number_raw": _s(d.get("job_number_raw"), 64),
        "client_name": _s(d.get("client_name"), 255), "project_name": _s(d.get("project_name"), 255),
        "stage": stage, "stage_flag": flag, "status_raw": _s(d.get("status_raw"), 64),
        "bid_due": rules.to_date(d.get("bid_due")), "submitted_on": rules.to_date(d.get("submitted_on")), "awarded_on": rules.to_date(d.get("awarded_on")),
        "start": rules.to_date(d.get("start")), "end": rules.to_date(d.get("end")),
        "budget": rules.to_decimal(d.get("budget")), "value": rules.to_decimal(d.get("value")),
        "bidder_raw": _s(d.get("bidder_raw"), 64), "sales_rep_raw": _s(d.get("sales_rep_raw"), 64), "house_account": rules.is_house_rep(d.get("sales_rep_raw")),
        "probability": rules.probability_from(d.get("probability_raw")), "pct_close_raw": "",
        "ball_in_court": "", "bom_status": "", "walkthrough_date": None, "walkthrough_raw": "",
        "po_number": _s(d.get("po_number"), 64), "sales_order": _s(d.get("sales_order"), 32), "comments": "", "attachments_flag": False,
        "created_by_raw": "", "portal_created": parse_datetime(it.get("createdDateTime")) if it.get("createdDateTime") else None,
        "portal_modified": parse_datetime(it.get("lastModifiedDateTime")) if it.get("lastModifiedDateTime") else None,
        "web_url": _s(it.get("webUrl"), 500), "pm_raw": _s(d.get("pm_raw"), 64),
    }
    return _clamp(out)


def _upsert_bids(rows, source, run):
    """rows: [(sp_item_id, dict)] -> Bid rows keyed on (source, sp_item_id); unchanged rows (content hash) are skipped."""
    stats = Counter()
    existing = {b.sp_item_id: b for b in Bid.objects.filter(source=source)}
    seen = set()
    with transaction.atomic():
        for sp_id, d in rows:
            seen.add(sp_id)
            h = content_hash(*[str(d.get(c)) for c in BID_COLS])
            b = existing.get(sp_id)
            if b is None:
                b = Bid(source=source, sp_item_id=sp_id, **{c: d.get(c) for c in BID_COLS})
                b.content_hash = h; b.last_seen_run = run
                b.save()
                stats["new"] += 1
            elif b.content_hash != h:
                for c in BID_COLS:
                    setattr(b, c, d.get(c))
                b.content_hash = h; b.last_seen_run = run
                b.save()
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        gone = [b for sid, b in existing.items() if sid not in seen]
        if gone and len(seen) > 0.5 * max(len(existing), 1):   # a partial pull must not delete the world
            Bid.objects.filter(pk__in=[b.pk for b in gone]).delete()
            stats["deleted"] = len(gone)
    return dict(stats)




def load_project_list(run):
    sid = _portal()
    clients = load_clients(run)
    lst = g.list_by_name(sid, LIST_NAME)
    items = g.list_items(sid, lst["id"], LIST_FIELDS, label="project_list")
    rows = [(str(it["id"]), _row_from_list(it, clients)) for it in items]
    stats = _upsert_bids(rows, Bid.Source.LIST, run)
    stats["rows"] = len(rows)
    _seed_client_aliases(clients.values())
    return stats


def load_project_archive(run):
    sid = _portal()
    lst = g.list_by_name(sid, ARCHIVE_NAME)
    items = g.list_items(sid, lst["id"], None, label="project_archive")
    rows = [(str(it["id"]), _row_from_archive(it)) for it in items]
    stats = _upsert_bids(rows, Bid.Source.ARCHIVE, run)
    stats["rows"] = len(rows)
    return stats


# ------------------------------------------------------------------------------------------- versions
def load_versions(run, limit=1500):
    """BidVersion for list rows whose portal_modified moved since the last pull (first pull = one call per row, capped
    per run so a refresh never runs for an hour; the next run continues)."""
    sid = _portal()
    lst = g.list_by_name(sid, LIST_NAME)
    todo = list(Bid.objects.filter(source=Bid.Source.LIST).filter(
        models_q_versions_stale()).order_by("-portal_modified")[:limit])
    stats = Counter(rows=0, versions=0, errors=0)
    for b in todo:
        try:
            vs = g.item_versions(sid, lst["id"], b.sp_item_id, VERSION_FIELDS)
        except g.GraphError as e:
            stats["errors"] += 1
            log.warning("versions for bid %s failed: %s", b.sp_item_id, e)
            continue
        objs = []
        for v in vs:
            f = v.get("fields") or {}
            stage, _ = rules.normalize_status(f.get("Status"))
            objs.append(BidVersion(bid=b, version_no=_s(v.get("id"), 16), modified_at=parse_datetime(v.get("lastModifiedDateTime")),
                                   modified_by=_s(((v.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"), 128),
                                   stage=stage, status_raw=_s(f.get("Status"), 64), value=rules.to_decimal(f.get("ProjectValue")),
                                   budget=rules.to_decimal(f.get("Budget")), bidder_raw=_s(f.get("Bidder"), 64),
                                   probability=rules.probability_from(f.get("Probability_x0020_of_x0020_Close")), job_number_raw=_s(f.get("Title"), 64)))
        with transaction.atomic():
            BidVersion.objects.filter(bid=b).delete()
            BidVersion.objects.bulk_create([o for o in objs if o.modified_at])
            Bid.objects.filter(pk=b.pk).update(versions_synced_at=b.portal_modified or timezone.now())
        stats["rows"] += 1
        stats["versions"] += len(objs)
    stats["remaining"] = Bid.objects.filter(source=Bid.Source.LIST).filter(models_q_versions_stale()).count()
    return dict(stats)


def models_q_versions_stale():
    from django.db.models import F, Q
    return Q(versions_synced_at__isnull=True) | Q(portal_modified__gt=F("versions_synced_at"))


# ------------------------------------------------------------------------------------------- linking (§2, §5.1)
def link_to_sl(run):
    """Bid.project from the typed Job Number (000000-aware), won_by_sl from SL billings, stage timing untouched."""
    projects = {r["cpn"]: r for r in fetch_dict("""
        SELECT p.id, p.canonical_project_number cpn, p.billed_revenue billed, d.code div
        FROM core_project p JOIN core_division d ON d.id = p.division_id""")}
    known = set(projects)
    stats = Counter()
    updates = []
    for b in Bid.objects.all().only("id", "job_number_raw", "project_id", "won_by_sl", "stage"):
        cpn = rules.resolve_job_number(b.job_number_raw, known) if b.job_number_raw else None
        pid = projects[cpn]["id"] if cpn else None
        won = bool(cpn and (projects[cpn]["billed"] or 0) > 0)
        if b.project_id != pid or b.won_by_sl != won:
            b.project_id, b.won_by_sl = pid, won
            updates.append(b)
        stats["linked" if pid else ("unmatched" if b.job_number_raw else "no_job")] += 1
        if won and b.stage not in (rules.AWARDED,):
            stats["won_by_sl_override"] += 1
    Bid.objects.bulk_update(updates, ["project_id", "won_by_sl"], batch_size=1000)
    stats["changed"] = len(updates)
    return dict(stats)


# ------------------------------------------------------------------------------------------- aliases (§4.1)
def _employee_pool():
    rows = fetch_dict("""
        SELECT e.id, e.canonical_name name, e.active, e.ptt_employee_role role, e.home_subaccount sub,
               (SELECT COUNT(*) FROM core_project p WHERE p.project_manager_id = e.id) n_pm,
               (SELECT COUNT(*) FROM core_project p WHERE p.salesperson_id IN (SELECT s.id FROM core_salesperson s WHERE s.employee_id = e.id)) n_sales,
               (SELECT d.code FROM core_project p JOIN core_division d ON d.id = p.division_id WHERE p.project_manager_id = e.id
                GROUP BY d.code ORDER BY COUNT(*) DESC LIMIT 1) top_div
        FROM core_employee e WHERE e.canonical_name <> ''""")
    # duplicate employee rows (Bruce Driftwood x3): prefer the active PTT-linked one
    best = {}
    for r in rows:
        k = rules.norm_name_key(r["name"])
        cur = best.get(k)
        if cur is None or (r["n_pm"] + r["n_sales"], r["active"], r["role"] in ("pm", "head_pm")) > (cur["n_pm"] + cur["n_sales"], cur["active"], cur["role"] in ("pm", "head_pm")):
            best[k] = r
    return list(best.values())


def resolve_aliases(run):
    """BidderAlias / BidClientAlias rows for every distinct raw value; manual rows are never touched."""
    pool = _employee_pool()
    by_name = {rules.norm_name_key(e["name"]): e for e in pool}
    stats = Counter()
    for role, col in (("estimator", "bidder_raw"), ("sales_rep", "sales_rep_raw"), ("walkthrough", "walkthrough_raw"), ("pm", None)):
        if col:
            raws = Counter(rules.norm_name_key(v) for v in Bid.objects.exclude(**{col: ""}).values_list(col, flat=True))
        else:
            raws = Counter(rules.norm_name_key(v) for v in Bid.objects.exclude(pm_raw="").values_list("pm_raw", flat=True))
        existing = {a.raw: a for a in BidderAlias.objects.filter(role=role)}
        for raw, n in raws.items():
            if not raw or raw in rules.NOT_A_PERSON or (role == "sales_rep" and raw in rules.HOUSE_REPS):
                a = existing.get(raw)
                if a and not a.manual:          # a rule change made this raw a non-person: drop the stale link
                    a.delete(); stats["dropped"] += 1
                continue
            a = existing.get(raw)
            if a and a.manual:
                a.uses = n; a.save(update_fields=["uses", "updated_at"]); stats["manual"] += 1
                continue
            cands = rules.alias_candidates(raw, pool)
            top = cands[0] if cands else None
            emp = by_name.get(rules.norm_name_key(top[0])) if top and top[1] >= 0.8 else None
            fields = dict(employee_id=emp["id"] if emp else None, confidence=Decimal(str(top[1])) if top else Decimal(0),
                          rule=top[2] if top else "none", candidates=[list(c) for c in cands[:5]], uses=n)
            if a is None:
                BidderAlias.objects.create(raw=raw, role=role, **fields)
            else:
                for k, v in fields.items():
                    setattr(a, k, v)
                a.save()
            stats["resolved" if emp else "unresolved"] += 1
    stats.update(_resolve_clients())
    return dict(stats)


def _seed_client_aliases(names):
    have = set(BidClientAlias.objects.values_list("raw", flat=True))
    BidClientAlias.objects.bulk_create([BidClientAlias(raw=n) for n in set(names) if n and n not in have], ignore_conflicts=True)


_SUFFIX = re.compile(r"\b(INC|LLC|LTD|CO|CORP|CORPORATION|COMPANY|THE|L\.?L\.?C\.?|INCORPORATED)\b\.?", re.I)


def _client_key(name):
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", name or "").upper()
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _resolve_clients():
    """Client name -> core_customer. Strongest signal first: the SL customer of the jobs this client's bids link to
    (>= 2 linked bids and >= 70 % on one customer); then an exact name match (suffixes stripped); then trigram
    similarity >= 0.6 (0.8 confidence); 0.4-0.6 is stored as a candidate for review; below that unresolved."""
    customers = fetch_dict("SELECT id, canonical_name FROM core_customer")
    by_key = defaultdict(list)
    for c in customers:
        by_key[_client_key(c["canonical_name"])].append(c)
    uses = Counter(Bid.objects.exclude(client_name="").values_list("client_name", flat=True))
    linked = defaultdict(Counter)
    for r in fetch_dict("""SELECT b.client_name, p.customer_id, COUNT(*) n FROM bids_bid b JOIN core_project p ON p.id = b.project_id
                           WHERE b.client_name <> '' AND p.customer_id IS NOT NULL GROUP BY 1, 2"""):
        linked[r["client_name"]][r["customer_id"]] += r["n"]
    sim = {}
    for r in fetch_dict("""SELECT a.raw, c.id, c.canonical_name, similarity(upper(c.canonical_name), upper(a.raw)) s
                           FROM bids_bidclientalias a JOIN LATERAL (SELECT id, canonical_name FROM core_customer
                               ORDER BY similarity(upper(canonical_name), upper(a.raw)) DESC LIMIT 1) c ON TRUE
                           WHERE NOT a.manual"""):
        sim[r["raw"]] = (r["id"], r["canonical_name"], float(r["s"] or 0))
    stats = Counter()
    for a in BidClientAlias.objects.all():
        a.uses = uses.get(a.raw, 0)
        if a.manual:
            a.save(update_fields=["uses", "updated_at"]); continue
        lk = linked.get(a.raw)
        hits = by_key.get(_client_key(a.raw), [])
        best = sim.get(a.raw)
        if lk and sum(lk.values()) >= 2 and lk.most_common(1)[0][1] / sum(lk.values()) >= 0.7:
            a.customer_id, a.confidence, a.rule = lk.most_common(1)[0][0], Decimal("0.9"), "linked_jobs"
            stats["client_resolved"] += 1
        elif len(hits) == 1:
            a.customer_id, a.confidence, a.rule = hits[0]["id"], Decimal("0.95"), "name"
            stats["client_resolved"] += 1
        elif len(hits) > 1:
            a.customer_id, a.confidence, a.rule = hits[0]["id"], Decimal("0.6"), "name_ambiguous"
            stats["client_ambiguous"] += 1
        elif best and best[2] >= 0.6:
            a.customer_id, a.confidence, a.rule = best[0], Decimal("0.8"), "similar"
            stats["client_resolved"] += 1
        elif best and best[2] >= 0.4:
            a.customer_id, a.confidence, a.rule = None, Decimal(str(round(best[2], 2))), "similar_candidate"
            stats["client_unresolved"] += 1
        else:
            a.customer_id, a.confidence, a.rule = None, Decimal(0), "none"
            stats["client_unresolved"] += 1
        a.candidates = [[best[0], best[1], round(best[2], 2)]] if best and best[2] >= 0.3 else []
        a.save()
    return stats


def apply_aliases(run):
    """Write estimator / salesperson / client onto every bid from the alias tables; infer blank bidders (§4.1); derive
    division (§4.2); write core_project.estimator for linked, non-inferred bids."""
    est = {a.raw: a for a in BidderAlias.objects.filter(role="estimator")}
    rep = {a.raw: a for a in BidderAlias.objects.filter(role="sales_rep")}
    creators = {a.raw: a for a in BidderAlias.objects.filter(role="pm")}
    clients = {a.raw: a for a in BidClientAlias.objects.exclude(customer_id=None)}
    known_estimators = {a.employee_id for a in est.values() if a.employee_id}
    emp_rows = {e["id"]: e for e in _employee_pool()}
    sub2div = {}
    for d in Division.objects.all():
        for s in (d.sl_subaccounts or []):
            sub2div[str(s)] = d.code
    proj_pm = {r["id"]: (r["pm"], r["div"]) for r in fetch_dict("SELECT p.id, p.project_manager_id pm, d.code div FROM core_project p JOIN core_division d ON d.id = p.division_id")}
    stats = Counter()
    updates = []
    for b in Bid.objects.all().only("id", "bidder_raw", "sales_rep_raw", "client_name", "created_by_raw", "project_id", "stage", "won_by_sl",
                                     "estimator_id", "estimator_inferred", "estimator_rule", "salesperson_id", "client_id", "division"):
        a = est.get(rules.norm_name_key(b.bidder_raw))
        new_est, inferred, rule = (a.employee_id, False, a.rule) if a and a.employee_id else (None, False, "")
        if new_est is None and not b.bidder_raw:
            r = rep.get(rules.norm_name_key(b.sales_rep_raw))
            if r and r.employee_id and r.employee_id in known_estimators:
                new_est, inferred, rule = r.employee_id, True, "inferred_rep"
            elif b.project_id and (b.stage == rules.AWARDED or b.won_by_sl) and proj_pm.get(b.project_id, (None,))[0] in known_estimators:
                new_est, inferred, rule = proj_pm[b.project_id][0], True, "inferred_pm"
            else:
                c = creators.get(rules.norm_name_key(b.created_by_raw)) or est.get(rules.norm_name_key(b.created_by_raw))
                if c and c.employee_id:
                    new_est, inferred, rule = c.employee_id, True, "inferred_creator"
        r = rep.get(rules.norm_name_key(b.sales_rep_raw))
        new_rep = r.employee_id if r and r.employee_id else None
        ca = clients.get(b.client_name)
        new_client = ca.customer_id if ca else None
        e = emp_rows.get(new_est) if new_est else None
        new_div = rules.division_for(proj_pm.get(b.project_id, (None, ""))[1] if b.project_id else "", e["top_div"] if e else "",
                                     e["sub"] if e else "", sub2div)
        if (b.estimator_id, b.estimator_inferred, b.estimator_rule, b.salesperson_id, b.client_id, b.division) != (new_est, inferred, rule, new_rep, new_client, new_div):
            b.estimator_id, b.estimator_inferred, b.estimator_rule, b.salesperson_id, b.client_id, b.division = new_est, inferred, rule, new_rep, new_client, new_div
            updates.append(b)
        stats["estimator" if new_est and not inferred else ("estimator_inferred" if new_est else "estimator_none")] += 1
    Bid.objects.bulk_update(updates, ["estimator_id", "estimator_inferred", "estimator_rule", "salesperson_id", "client_id", "division"], batch_size=1000)
    stats["changed"] = len(updates)
    # last resort for division (§4.2 step 4): the estimator's dominant division across their own linked bids -- covers
    # estimators who never PM'd a job (Driftwood, Glenbrook, Hazelton) and have no home subaccount mapping.
    with connection.cursor() as cur:
        cur.execute("""UPDATE bids_bid b SET division = x.div FROM (
                           SELECT estimator_id, division div FROM (
                               SELECT estimator_id, division, ROW_NUMBER() OVER (PARTITION BY estimator_id ORDER BY COUNT(*) DESC) rn
                               FROM bids_bid WHERE division <> '' AND estimator_id IS NOT NULL GROUP BY 1, 2) t WHERE rn = 1) x
                       WHERE b.estimator_id = x.estimator_id AND b.division = ''""")
        stats["division_from_own_bids"] = cur.rowcount
    stats.update(write_project_estimators())
    return dict(stats)


def write_project_estimators():
    """core_project.estimator = the estimator of the awarded (or won-by-SL) bid; latest bid wins when several link.
    Inferred estimators are not written (ratings must not rest on guesses)."""
    rows = fetch_dict("""
        SELECT DISTINCT ON (b.project_id) b.project_id, b.estimator_id
        FROM bids_bid b WHERE b.project_id IS NOT NULL AND b.estimator_id IS NOT NULL AND NOT b.estimator_inferred
          AND (b.stage = 'awarded' OR b.won_by_sl)
        ORDER BY b.project_id, b.portal_modified DESC NULLS LAST""")
    want = {r["project_id"]: r["estimator_id"] for r in rows}
    have = dict(Project.objects.filter(pk__in=want).values_list("id", "estimator_id"))
    changed = [Project(id=pid, estimator_id=eid) for pid, eid in want.items() if have.get(pid) != eid]
    Project.objects.bulk_update(changed, ["estimator_id"], batch_size=1000)
    return {"project_estimators": len(want), "project_estimators_changed": len(changed)}


# ------------------------------------------------------------------------------------------- snapshot (§3)
def snapshot_pipeline(run, day=None):
    day = day or timezone.localdate()
    agg = defaultdict(lambda: {"count": 0, "value": Decimal(0), "weighted": Decimal(0), "a30": 0, "a60": 0})
    for b in Bid.objects.all().only("division", "estimator_id", "stage", "value", "probability", "portal_modified"):
        k = (b.division or "", b.estimator_id, b.stage)
        a = agg[k]
        a["count"] += 1
        a["value"] += b.value or 0
        a["weighted"] += (b.value or 0) * Decimal(b.probability or 0) / 100
        if b.stage in rules.OPEN_STAGES and b.portal_modified:
            age = (timezone.now() - b.portal_modified).days
            a["a30"] += age >= 30
            a["a60"] += age >= 60
    with transaction.atomic():
        BidSnapshot.objects.filter(snapshot_date=day).delete()
        BidSnapshot.objects.bulk_create([BidSnapshot(snapshot_date=day, division=k[0], estimator_id=k[1], stage=k[2], count=a["count"], value=a["value"],
                                                     weighted_value=a["weighted"], aged_30=a["a30"], aged_60=a["a60"]) for k, a in agg.items()])
    return {"rows": len(agg), "date": str(day)}


# ------------------------------------------------------------------------------------------- data quality (§12)
def check_quality(run):
    today = timezone.localdate()
    n = Counter()
    for a in BidderAlias.objects.filter(role="estimator", employee_id=None):
        issue(run, "bid_bidder_unresolved", "warning", source_system="sharepoint", source_key=a.raw, uses=a.uses, candidates=a.candidates); n["bidder_unresolved"] += 1
    for a in BidderAlias.objects.filter(role="sales_rep", employee_id=None):
        issue(run, "bid_rep_unresolved", "info", source_system="sharepoint", source_key=a.raw, uses=a.uses); n["rep_unresolved"] += 1
    for a in BidClientAlias.objects.filter(customer_id=None, uses__gt=0):
        issue(run, "bid_client_unresolved", "info", source_system="sharepoint", source_key=a.raw[:255], uses=a.uses); n["client_unresolved"] += 1
    cutoff = today - timedelta(days=14)
    recent = today - timedelta(days=730)
    for b in Bid.objects.filter(stage="awarded", project=None, source="list").only("id", "job_number_raw", "project_name", "awarded_on", "portal_modified", "sp_item_id"):
        when = b.awarded_on or (b.portal_modified.date() if b.portal_modified else None)
        if when and when < recent:
            continue
        if b.job_number_raw and when and when <= cutoff:
            issue(run, "bid_job_not_in_sl", "warning", source_system="sharepoint", source_key=b.sp_item_id, job=b.job_number_raw, name=b.project_name); n["job_not_in_sl"] += 1
        elif not b.job_number_raw and when and when <= cutoff:
            issue(run, "bid_awarded_no_job", "warning", source_system="sharepoint", source_key=b.sp_item_id, name=b.project_name, awarded=str(when)); n["awarded_no_job"] += 1
    for b in Bid.objects.filter(won_by_sl=True).exclude(stage="awarded").only("id", "sp_item_id", "status_raw", "project_id", "project_name"):
        issue(run, "bid_won_by_sl_override", "info", project_id=b.project_id, source_system="sharepoint", source_key=b.sp_item_id, portal_status=b.status_raw, name=b.project_name); n["won_by_sl"] += 1
    for r in fetch_dict("""SELECT b.sp_item_id, b.project_id, b.value, p.contract_value cv, b.project_name FROM bids_bid b JOIN core_project p ON p.id = b.project_id
                           WHERE b.value > 0 AND p.contract_value > 0 AND ABS(b.value - p.contract_value) / p.contract_value > 0.05 AND (b.stage = 'awarded' OR b.won_by_sl)"""):
        issue(run, "bid_value_vs_cv", "info", project_id=r["project_id"], source_system="sharepoint", source_key=r["sp_item_id"], portal_value=float(r["value"]), contract_value=float(r["cv"]), name=r["project_name"]); n["value_vs_cv"] += 1
    for r in fetch_dict("""SELECT client_name, project_name, bid_due, COUNT(*) n, MIN(sp_item_id) k FROM bids_bid WHERE source='list' AND project_name <> ''
                           GROUP BY 1,2,3 HAVING COUNT(*) > 1"""):
        issue(run, "bid_duplicate", "info", source_system="sharepoint", source_key=r["k"], client=r["client_name"], name=r["project_name"], due=str(r["bid_due"]), copies=r["n"]); n["duplicate"] += 1
    tot = Bid.objects.filter(source="list").count()
    filled = Bid.objects.filter(source="list", probability__isnull=False).count()
    pct = Bid.objects.filter(source="list").exclude(pct_close_raw="").count()
    issue(run, "bid_probability_fill", "info", source_system="sharepoint", source_key="project_list", rows=tot, probability_of_close_filled=filled, pct_of_close_filled=pct)
    blank = Bid.objects.filter(source="list", bidder_raw="").count()
    issue(run, "bid_bidder_blank", "info", source_system="sharepoint", source_key="project_list", rows=blank, inferred=Bid.objects.filter(source="list", estimator_inferred=True).count())
    return dict(n)


# ------------------------------------------------------------------------------------------- driver
def load_all(run, versions_limit=1500):
    out = {"audit": g.permissions_audit()}
    out["list"] = load_project_list(run)
    out["archive"] = load_project_archive(run)
    out["link"] = link_to_sl(run)
    out["aliases"] = resolve_aliases(run)
    out["apply"] = apply_aliases(run)
    out["versions"] = load_versions(run, limit=versions_limit)
    out["snapshot"] = snapshot_pipeline(run)
    out["quality"] = check_quality(run)
    return out
