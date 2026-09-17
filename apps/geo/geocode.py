"""Address normalisation, the choice of one address per project, and the two geocoders
(docs/project_map_plan.md §1). Pure helpers first (unit-tested), network clients last.

Geocoders: US Census batch endpoint (free, no key, 10,000 rows per request) for street addresses;
Nominatim (OpenStreetMap) for the misses and for city centroids — 1 request/second with an
identifying User-Agent, as its usage policy asks. Results are cached in geo_geocodedaddress.
"""

import os
import csv
import io
import re
import time

import requests

CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CENSUS_ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = os.environ.get("PCA_GEOCODER_USER_AGENT", "PaceCompanyAnalytics/1.0")
CENSUS_BATCH_SIZE = 9000
NOMINATIM_MIN_INTERVAL = 1.1        # seconds between requests

# Pace's own addresses — an order shipped to the warehouse says nothing about the job site.
PACE_OWN_STREETS = ("2040 CORPORATE",)
_WS = re.compile(r"\s+")
_HAS_DIGIT = re.compile(r"\d")
_PO_BOX = re.compile(r"\bP\.?\s*O\.?\s*BOX\b", re.IGNORECASE)


def clean(s):
    return _WS.sub(" ", (s or "").replace(" ", " ")).strip()


def pick_street(addr1, addr2):
    """SL users put the street on either line (and building names on the other). Prefer the line
    with a house number; fall back to the other; PO boxes are not places."""
    a1, a2 = clean(addr1), clean(addr2)
    cands = [a for a in (a1, a2) if a and not _PO_BOX.search(a)]
    for a in cands:
        if _HAS_DIGIT.match(a) or _HAS_DIGIT.search(a[:6]):
            return a
    for a in cands:
        if _HAS_DIGIT.search(a):
            return a
    return cands[0] if cands else ""


def split_city_state(city, state):
    """'HOFFMAN ESTATES, IL' with a blank state happens; pull the state back out."""
    c, s = clean(city), clean(state).upper()
    m = re.match(r"^(.*?)[,\s]+([A-Z]{2})$", c.upper())
    if not s and m:
        return clean(m.group(1)).title(), m.group(2)
    return c.title(), s


def normalize(street, city, state, zip_code):
    """(street, city, state, zip5, key) — key is the cache key; None when there is nothing to geocode."""
    street = clean(street).upper().rstrip(".,")
    city, state = split_city_state(city, state)
    z = re.sub(r"[^0-9]", "", clean(zip_code))[:5]
    if not (street or city):
        return None
    key = " | ".join(p for p in (street, city.upper(), state, z) if p)[:200]
    return street, city, state, z, key


def is_pace_own(street):
    s = clean(street).upper()
    return any(p in s for p in PACE_OWN_STREETS)


def choose_project_address(site_rows, customer_row):
    """Best address for one project. site_rows: dicts from geo_projectsiteaddress (any order);
    customer_row: dict from geo_customeraddress or None. Returns (source, street, city, state, zip,
    site_name) or None. Most-used ship-to wins (ties → most recent); Pace's own warehouse and rows
    with no street are skipped; then the customer's street; then the customer's city."""
    ranked = sorted(site_rows, key=lambda r: (-(r.get("orders") or 0), -(r.get("last_order_date").toordinal() if r.get("last_order_date") else 0)))
    for r in ranked:
        street = pick_street(r.get("addr1"), r.get("addr2"))
        if not street or is_pace_own(street):
            continue
        return ("site", street, r.get("city") or "", r.get("state") or "", r.get("zip") or "", r.get("site_name") or "")
    return _customer_fallback(customer_row)


def _customer_fallback(customer_row):
    if customer_row:
        street = pick_street(customer_row.get("addr1"), customer_row.get("addr2"))
        if street and not is_pace_own(street):
            return ("customer", street, customer_row.get("city") or "", customer_row.get("state") or "", customer_row.get("zip") or "", customer_row.get("name") or "")
        if clean(customer_row.get("city")):
            return ("city", "", customer_row.get("city") or "", customer_row.get("state") or "", customer_row.get("zip") or "", customer_row.get("name") or "")
    return None


_UNIT = re.compile(r"[\s,;]*(?:\b(?:SUITE|STE|UNIT|APT|FLOOR|FL|RM|ROOM|BLDG|DOCK|DEPT|MS)\b\.?\s*#?\s*|#\s*)[\w./-]+\s*$", re.IGNORECASE)


def strip_unit(street):
    """'180 N. WABASH; SUITE 200' -> '180 N. WABASH': CNET ship-to lines carry suites/docks the geocoders
    trip over. Only a trailing unit is removed and never the whole line."""
    s = clean(street)
    out = _UNIT.sub("", s).strip(" ,;")
    return out if out and _HAS_DIGIT.search(out) else s


def choose_sales_address(doc, customer_row):
    """Best address for one 010 hardware sales order (the map's 010 layer). doc: dict with ship_addr1,
    ship_city, ship_state, ship_zip, ship_company (CNET's ship-to); customer_row as for projects.
    The CNET ship-to street wins (Pace's own warehouse — will-call — and blank streets fall through),
    then the customer's street, then the customer's city. Returns (source, street, city, state, zip,
    site_name) or None."""
    street = pick_street(doc.get("ship_addr1"), "")
    if street and not is_pace_own(street):
        return ("ship", strip_unit(street), doc.get("ship_city") or "", doc.get("ship_state") or "", doc.get("ship_zip") or "", doc.get("ship_company") or "")
    return _customer_fallback(customer_row)


def parse_census_batch(text):
    """Census batch CSV -> {id: (ok, lat, lng, match_type, matched_address)}. Columns: id, input,
    match flag (Match/No_Match/Tie), match type (Exact/Non_Exact), matched address, 'lng,lat', tiger id, side."""
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        rid, flag = row[0], row[2]
        if flag == "Match" and len(row) >= 6 and row[5]:
            lng, lat = row[5].split(",")
            out[rid] = (True, float(lat), float(lng), row[3], row[4])
        else:
            out[rid] = (False, None, None, flag, "")
    return out


# ------------------------------------------------------------------ network clients
def census_batch(items, timeout=180):
    """items: [(id, street, city, state, zip)] -> parse_census_batch dict. Empty on transport error."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for rid, street, city, state, z in items:
        w.writerow([rid, street, city, state, z])
    files = {"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")}
    r = requests.post(CENSUS_BATCH_URL, files=files, data={"benchmark": "Public_AR_Current"},
                      headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return parse_census_batch(r.text)


def census_oneline(address, timeout=30):
    """Single address -> (lat, lng, match_type, matched_address) or None."""
    r = requests.get(CENSUS_ONELINE_URL, params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
                     headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    matches = r.json().get("result", {}).get("addressMatches") or []
    if not matches:
        return None
    m = matches[0]
    return m["coordinates"]["y"], m["coordinates"]["x"], m.get("tigerLine", {}).get("side", "") and "Match", m.get("matchedAddress", "")


class Nominatim:
    """Rate-limited Nominatim client (1 req/s, identifying UA)."""

    def __init__(self):
        self._last = 0.0

    def search(self, q, timeout=30, countrycodes="us,ca"):
        wait = NOMINATIM_MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = requests.get(NOMINATIM_URL, params={"q": q, "format": "jsonv2", "limit": 1, "countrycodes": countrycodes},
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        hits = r.json()
        if not hits:
            return None
        h = hits[0]
        return float(h["lat"]), float(h["lon"]), "%s/%s" % (h.get("class", ""), h.get("type", "")), h.get("display_name", "")[:160]
