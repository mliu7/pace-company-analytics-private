"""Project locations for the map: pull the two SL address sources, choose one address per project,
geocode what is new (docs/project_map_plan.md §1, §3). Local writes only."""

import logging
from collections import defaultdict

from django.db import connection, transaction
from django.utils import timezone

from apps.core.models import Customer, Project
from apps.geo import geocode as G
from apps.geo.models import CustomerAddress, GeocodedAddress, ProjectLocation, ProjectSiteAddress, SalesOrderLocation
from apps.ingestion.bulk import fetch_dict
from apps.ingestion.sources import sl_client

log = logging.getLogger(__name__)
NOMINATIM_CAP = 300          # per run; the rest waits for the next run (1 req/s policy)


def _s(v, n):
    return (v or "").strip()[:n]


def load_site_addresses(run):
    """sl.project_site_addresses -> geo_projectsiteaddress (full replace)."""
    pmap = dict(Project.objects.values_list("canonical_project_number", "id"))
    rows = []
    for r in sl_client.iter_rows("sl.project_site_addresses"):
        key = (r["project_number_raw"] or "").strip().upper()
        rows.append(ProjectSiteAddress(project_id=pmap.get(key), project_number_raw=key[:32], sl_customer_id=_s(r["sl_customer_id"], 15),
                                       ship_to_id=_s(r["ship_to_id"], 15), orders=r["orders"] or 0,
                                       last_order_date=r["last_order_date"].date() if r["last_order_date"] else None,
                                       site_name=_s(r["site_name"], 64), addr1=_s(r["addr1"], 64), addr2=_s(r["addr2"], 64),
                                       city=_s(r["city"], 32), state=_s(r["state"], 8), zip=_s(r["zip"], 12), country=_s(r["country"], 8),
                                       ingestion_run=run))
    with transaction.atomic():
        ProjectSiteAddress.objects.all().delete()
        ProjectSiteAddress.objects.bulk_create(rows, batch_size=2000)
    return {"rows": len(rows), "linked": sum(1 for x in rows if x.project_id)}


def load_customer_addresses(run):
    """sl.customer_addresses -> geo_customeraddress (full replace)."""
    cmap = dict(Customer.objects.values_list("sl_customer_id", "id"))
    rows, seen = [], {}
    for r in sl_client.iter_rows("sl.customer_addresses"):
        cid = _s(r["sl_customer_id"], 15).upper()
        # SL has a few duplicate CustIds differing only by case/whitespace — keep the row with the fuller address
        score = sum(1 for k in ("addr1", "addr2", "city", "zip") if (r.get(k) or "").strip())
        if cid in seen and seen[cid][0] >= score:
            continue
        if cid in seen:
            rows.pop(seen[cid][1])
            seen = {k: (v[0], v[1] if v[1] < seen[cid][1] else v[1] - 1) for k, v in seen.items() if k != cid}
        seen[cid] = (score, len(rows))
        rows.append(CustomerAddress(customer_id=cmap.get(cid), sl_customer_id=cid, name=_s(r["name"], 64),
                                    addr1=_s(r["addr1"], 64), addr2=_s(r["addr2"], 64), city=_s(r["city"], 32), state=_s(r["state"], 8),
                                    zip=_s(r["zip"], 12), country=_s(r["country"], 8), bill_addr1=_s(r["bill_addr1"], 64),
                                    bill_city=_s(r["bill_city"], 32), bill_state=_s(r["bill_state"], 8), bill_zip=_s(r["bill_zip"], 12),
                                    ingestion_run=run))
    with transaction.atomic():
        CustomerAddress.objects.all().delete()
        CustomerAddress.objects.bulk_create(rows, batch_size=2000)
    return {"rows": len(rows)}


def resolve_addresses():
    """{project_id: (source, street, city, state, zip, site_name, key)} for every project we can place."""
    sites = defaultdict(list)
    for r in fetch_dict("SELECT project_id, orders, last_order_date, site_name, addr1, addr2, city, state, zip FROM geo_projectsiteaddress WHERE project_id IS NOT NULL"):
        sites[r["project_id"]].append(r)
    cust = {r["customer_id"]: r for r in fetch_dict("SELECT customer_id, name, addr1, addr2, city, state, zip FROM geo_customeraddress WHERE customer_id IS NOT NULL")}
    out = {}
    for pid, cid in Project.objects.filter(is_template_or_void=False).values_list("id", "customer_id"):
        choice = G.choose_project_address(sites.get(pid, []), cust.get(cid))
        if not choice:
            continue
        source, street, city, state, z, site_name = choice
        norm = G.normalize(street, city, state, z)
        if not norm:
            continue
        street, city, state, z, key = norm
        out[pid] = (source, street, city, state, z, site_name, key)
    return out


def geocode_new(keys_needed, nominatim_cap=NOMINATIM_CAP, use_census=True):
    """Geocode cache misses. keys_needed: {key: (street, city, state, zip)}. Returns counts."""
    have = set(GeocodedAddress.objects.filter(address_key__in=list(keys_needed)).values_list("address_key", flat=True))
    new = {k: v for k, v in keys_needed.items() if k not in have}
    GeocodedAddress.objects.bulk_create([GeocodedAddress(address_key=k, street=v[0][:100], city=v[1][:40], state=v[2][:8], zip=v[3][:12])
                                         for k, v in new.items()], batch_size=2000)
    stats = {"new": len(new), "census_ok": 0, "nominatim_ok": 0, "nominatim_tried": 0, "still_missing": 0}
    now = timezone.now()
    # 1) Census batch for street addresses never tried
    if use_census:
        todo = list(GeocodedAddress.objects.filter(ok=False, attempts=0).exclude(street=""))
        for i in range(0, len(todo), G.CENSUS_BATCH_SIZE):
            chunk = todo[i:i + G.CENSUS_BATCH_SIZE]
            try:
                res = G.census_batch([(str(g.id), g.street, g.city, g.state, g.zip) for g in chunk])
            except Exception as e:  # noqa
                log.warning("census batch failed: %s", e)
                res = {}
            for g in chunk:
                r = res.get(str(g.id))
                g.attempts += 1
                if r and r[0]:
                    g.ok, g.lat, g.lng, g.source, g.match_type, g.matched_address = True, r[1], r[2], "census", r[3], r[4][:160]
                    g.precision, g.geocoded_at = "street", now
                    stats["census_ok"] += 1
                elif r:
                    g.last_error = "census:" + r[3]
            GeocodedAddress.objects.bulk_update(chunk, ["ok", "lat", "lng", "source", "match_type", "matched_address", "precision", "attempts", "geocoded_at", "last_error"], batch_size=1000)
    # 2) Nominatim for the misses (street misses and city-only keys), capped per run
    nm = G.Nominatim()
    todo = list(GeocodedAddress.objects.filter(ok=False, attempts__lt=3).order_by("attempts", "id")[:nominatim_cap])
    for g in todo:
        q = ", ".join(p for p in (g.street.title() if g.street else "", g.city, g.state, g.zip) if p)
        stats["nominatim_tried"] += 1
        g.attempts += 1
        try:
            r = nm.search(q)
        except Exception as e:  # noqa
            g.last_error = ("nominatim:" + str(e))[:160]
            r = None
        if r:
            g.ok, g.lat, g.lng, g.source, g.match_type, g.matched_address = True, r[0], r[1], "nominatim", r[2][:24], r[3][:160]
            g.precision, g.geocoded_at = ("street" if g.street else "city"), now
            stats["nominatim_ok"] += 1
        g.save(update_fields=["ok", "lat", "lng", "source", "match_type", "matched_address", "precision", "attempts", "geocoded_at", "last_error"])
    stats["still_missing"] = GeocodedAddress.objects.filter(ok=False).count()
    return stats


def build_locations(run, resolved=None):
    """Write geo_projectlocation from the resolved addresses + cache. Manual rows are kept."""
    resolved = resolved or resolve_addresses()
    cache = {g.address_key: g for g in GeocodedAddress.objects.filter(ok=True, address_key__in=[v[6] for v in resolved.values()])}
    manual = set(ProjectLocation.objects.filter(source="manual").values_list("project_id", flat=True))
    existing = {l.project_id: l for l in ProjectLocation.objects.exclude(source="manual")}
    keep, create, update = set(), [], []
    for pid, (source, street, city, state, z, site_name, key) in resolved.items():
        if pid in manual:
            continue
        g = cache.get(key)
        if not g:
            continue
        quality = "city" if (source == "city" or g.precision == "city") else ("exact" if g.match_type == "Exact" else "approx")
        address = ", ".join(p for p in (street.title() if street else "", city, state, z) if p)[:200]
        fields = dict(lat=g.lat, lng=g.lng, source=source, quality=quality, address=address, site_name=site_name[:64], geocoded_address=g, ingestion_run=run)
        keep.add(pid)
        l = existing.get(pid)
        if l:
            changed = any(getattr(l, k) != v for k, v in fields.items() if k != "ingestion_run")
            if changed:
                for k, v in fields.items():
                    setattr(l, k, v)
                update.append(l)
        else:
            create.append(ProjectLocation(project_id=pid, **fields))
    with transaction.atomic():
        ProjectLocation.objects.exclude(source="manual").exclude(project_id__in=keep).delete()
        ProjectLocation.objects.bulk_create(create, batch_size=2000)
        ProjectLocation.objects.bulk_update(update, ["lat", "lng", "source", "quality", "address", "site_name", "geocoded_address", "ingestion_run"], batch_size=1000)
    by_source = {r["source"]: r["n"] for r in fetch_dict("SELECT source, COUNT(*) n FROM geo_projectlocation GROUP BY source")}
    return {"created": len(create), "updated": len(update), "located": ProjectLocation.objects.count(), "by_source": by_source,
            "unresolved": Project.objects.filter(is_template_or_void=False).count() - len(resolved)}


SALES_SQL = """SELECT d.id, d.customer_sl_id, d.ship_company, d.ship_addr1, d.ship_city, d.ship_state, d.ship_zip
               FROM sales_cnetdocument d WHERE d.doc_type = 'sales_order' AND NOT d.deleted"""


def resolve_sales_addresses():
    """{document_id: (source, street, city, state, zip, site_name, key)} for every 010 sales order we can place
    (CNET ship-to, else the customer's street, else the customer's city)."""
    cust = {r["sl_customer_id"]: r for r in fetch_dict("SELECT sl_customer_id, name, addr1, addr2, city, state, zip FROM geo_customeraddress")}
    out = {}
    for r in fetch_dict(SALES_SQL):
        choice = G.choose_sales_address(r, cust.get((r["customer_sl_id"] or "").strip().upper()))
        if not choice:
            continue
        source, street, city, state, z, site_name = choice
        norm = G.normalize(street, city, state, z)
        if not norm:
            continue
        street, city, state, z, key = norm
        out[r["id"]] = (source, street, city, state, z, site_name, key)
    return out


def build_sales_locations(run, resolved=None):
    """Write geo_salesorderlocation (full replace) from the resolved sales addresses + geocode cache."""
    resolved = resolved or resolve_sales_addresses()
    cache = {g.address_key: g for g in GeocodedAddress.objects.filter(ok=True, address_key__in=list({v[6] for v in resolved.values()}))}
    rows = []
    for did, (source, street, city, state, z, site_name, key) in resolved.items():
        g = cache.get(key)
        if not g:
            continue
        quality = "city" if (source == "city" or g.precision == "city") else ("exact" if g.match_type == "Exact" else "approx")
        address = ", ".join(p for p in (street.title() if street else "", city, state, z) if p)[:200]
        rows.append(SalesOrderLocation(document_id=did, lat=g.lat, lng=g.lng, source=source, quality=quality, address=address,
                                       site_name=(site_name or "")[:120], address_key=key, geocoded_address=g, ingestion_run=run))
    with transaction.atomic():
        SalesOrderLocation.objects.all().delete()
        SalesOrderLocation.objects.bulk_create(rows, batch_size=2000)
    by_source = {r["source"]: r["n"] for r in fetch_dict("SELECT source, COUNT(*) n FROM geo_salesorderlocation GROUP BY source")}
    return {"located": len(rows), "by_source": by_source, "unresolved": len(fetch_dict(SALES_SQL)) - len(resolved)}


def refresh_locations(run, pull=True, nominatim_cap=NOMINATIM_CAP, use_census=True):
    """The refresh step: pull addresses (optional), geocode new ones, rebuild project + 010 sales locations."""
    out = {}
    if pull:
        out["sites"] = load_site_addresses(run)
        out["customers"] = load_customer_addresses(run)
    resolved = resolve_addresses()
    sales = resolve_sales_addresses()
    out["resolved"] = len(resolved)
    out["sales_resolved"] = len(sales)
    keys = {v[6]: (v[1], v[2], v[3], v[4]) for v in resolved.values()}
    keys.update({v[6]: (v[1], v[2], v[3], v[4]) for v in sales.values()})
    out["geocode"] = geocode_new(keys, nominatim_cap=nominatim_cap, use_census=use_census)
    out["locations"] = build_locations(run, resolved)
    out["sales_locations"] = build_sales_locations(run, sales)
    return out
