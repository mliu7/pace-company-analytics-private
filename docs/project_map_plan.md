# Project Map — plan (2026-08-28)

**Goal.** A page (`/map/`) that shows every Pace project as a structure on a navigable 3D map of
Chicagoland (and beyond), sized by the project's scale, coloured by what you choose, with the crew's
whereabouts visible for any time window, filterable by division / state / type / sector / PM /
customer, and a timeline that plays the work back month by month. Browser only — no installs.

## 1. What we can locate a project with (verified in SL/PTT 2026-08-28)
| Source | Coverage | Quality | Notes |
|---|---|---|---|
| **Sales-order ship-to** (`SOHeader.ProjectID` → `SOAddress` by CustId+ShipToId) | 42,927 orders → **3,875 projects**, 1,717 distinct addresses (820 IL, rest across 50 states) | *site* — usually the job site, sometimes the GC's office | The most-used ship-to per project wins; ties → most recent. 4 Pace-warehouse ship-tos (2040 Corporate Lane) are skipped. |
| **Customer master address** (`Customer.Addr1/City/State/Zip`) | 2,021 of 2,081 customers | *customer* — the customer's own address (a hospital = the site; a GC = its office) | Fallback when a project has no orders. |
| **Customer city/state** | 2,048 customers | *city* — centroid only | Last resort; drawn hollow so it is obviously approximate. |
| `PJPROJ.shiptoid`, `PJADDR`, `PJSITE` | 6 / 9 / 0 rows | — | Not usable. |
| Manual fix | — | *manual* | Any project can be pinned by address or lat/lng from the map's detail panel; stored locally only. |

Geocoding: **US Census Geocoder** (free, no key, batch of 10,000; exact vs non-exact match flags)
first; **Nominatim (OpenStreetMap)** for the misses and for city centroids (1 request/s, identified
user agent, capped per run). Every result is cached locally (`geo_geocodedaddress`) so re-runs cost
nothing; `manage.py geocode_projects` pulls new addresses from SL and geocodes only what is new, and
`refresh_all` calls the same step.

## 2. What the map shows
- **Basemap**: OpenFreeMap vector tiles (free, no key) — *Positron* (light, matches the app) or
  *Dark*; real Chicago building footprints extruded in 3D, faint, so the towers read against them.
  Pitch/rotate/zoom with mouse or trackpad; "Chicagoland", "Fit visible", "Out of area" jumps.
- **Towers**: one extruded block per project at its location. **Footprint ∝ √(contract value)**
  (the "size of the project"), **height = the chosen metric** (contract value · hours in the window ·
  cost to date · crew size in the window) with an exaggeration slider. Projects at the same address
  (a hospital with twelve jobs) are laid out in a tight spiral around the point, largest in the
  middle, so every one is clickable — a campus rather than a pile.
- **Colour** by division · lifecycle state · solution class · margin (only when the viewer has
  margin access) · crew heat (hours in the window). Approximate locations (customer / city) draw with
  a dashed outline ring so nobody mistakes an office for a site.
- **Crew presence** for the window: a pulsing ring at the base of every project with hours, ring size
  = headcount, plus a "N crew · H h" label; a **People** panel lists everyone who logged hours, and
  selecting a person dims everything else, lifts their projects and draws their route between sites
  in date order (a technician's month at a glance).
- **Detail panel** on click: number, title, customer (link), PM, division, state, type, solution,
  sector, contract / billed / cost / GP (gated), PTT % complete, dates, crew in the window with hours,
  location provenance (source · quality · address) with a **fix location** control, link to the
  project page. Hover shows a compact tooltip.
- **Filters** (left panel, all combinable, mirrored in the URL so a view can be bookmarked): time
  window presets (this week · month · 90 days · this year · all time) + custom dates; division;
  lifecycle states; project type (mode); solution class; customer sector; PM; customer / number /
  title search; minimum contract value; "only projects with crew in the window"; person.
- **Timeline**: play the window month by month — towers rise as cumulative hours accumulate, rings
  pulse where people were that month, the stats strip counts along. Speed control, scrub bar.
- **Stats strip**: projects shown · contract value · hours and headcount in the window · unlocated
  count (with a list, so the gaps are visible and fixable).

## 3. Architecture
- `apps/geo` (new app): `ProjectSiteAddress`, `CustomerAddress` (full-replace copies of the two new
  registered SELECTs `sl.project_site_addresses`, `sl.customer_addresses`), `GeocodedAddress`
  (cache), `ProjectLocation` (one row per located project: lat/lng, source, quality, address text).
  `apps/geo/geocode.py` (address normalisation, choice rules, Census batch + Nominatim clients),
  `apps/ingestion/geo_loaders.py` (pull + resolve + geocode as one refresh step),
  `manage.py geocode_projects`.
- `apps/analytics/project_map.py`: the JSON payload — projects overlapping the window (with static
  attributes + location), presence (hours by project and person, by month for the timeline), option
  lists. Margin fields only when `acc.margins`.
- `apps/dashboard`: `project_map` (page), `project_map_data` (JSON), `project_map_locate` (POST manual
  fix — local table only). Access: `projects.view`. Nav: Performance → **Project Map**.
- Frontend: MapLibre GL JS (vendored, 0.8 MB) + `static/dashboard/project_map.js`; all styling in
  `app.css`. No build step, no CDN scripts — only map tiles and the geocoders are fetched from the
  internet.
- Never writes to SL/PTT. The only local writes are the geo tables and manual location fixes.

## 4. Build order
1. Registered SQL + guard entries; `apps/geo` models + migration; loaders; geocoder; command.
   Run the first geocode (Census batch ≈ 1 min, Nominatim tail a few minutes).
2. Payload builder + JSON view; page + map with towers, hover, click panel, filters, stats.
3. Presence rings/labels, People panel with route mode, timeline playback.
4. Manual location fix; out-of-area list; URL state; docs (`docs/08_project_map.md`), tests.

## 5. Known limits (stated on the page)
- A ship-to address is where the order shipped — usually the site, sometimes the GC's or the
  customer's office. Quality is shown per project and can be corrected in place.
- PTT hours are the only "where people are" signal (a job report = a person at that project that
  day); office/shop time is not on the map.
- Projects without any order and whose customer has no address are listed as unlocated.
