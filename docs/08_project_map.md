# Project Map (`/map/`)

A geographic explorer for projects and 010 hardware-sales customer sites, with broad, flat-roofed
buildings sized by site totals, mapped building footprints up close, crew activity and monthly replay.
The map opens on Chicagoland; Fit sites includes matching locations elsewhere in the country.
Plan and data survey: `docs/project_map_plan.md`. Browser only.

## Where a project's location comes from
`manage.py geocode_projects` (also runs inside `refresh_all` as the `geo_locations` step):
1. **Ship-to** — the most-used ship-to address across the project's sales orders
   (`sl.project_site_addresses`: `SOHeader.ProjectID` → `SOAddress`), ties → most recent; Pace's own
   warehouse (2040 Corporate Lane) and blank streets skipped. Source `site`.
2. **Customer address** (`sl.customer_addresses`, `Customer.Addr1/2/City/State/Zip`). Source `customer`.
3. **Customer city** only. Source `city`, quality `city` (identified in hover cards and location details).
4. **Manual** — pinned from the detail panel ("fix location": an address or `lat, lng`); never
   overwritten by the refresh; "Back to automatic" removes the pin.

Geocoding: US Census batch (`Exact` → quality `exact`, `Non_Exact` → `approx`), then Nominatim
(1 req/s, capped per run; also the city centroids). Cache `geo_geocodedaddress`; result
`geo_projectlocation`. First run 2026-08-28: 8,487 of 8,729 resolvable projects located
(3,757 site · 4,340 customer · 390 city); 262 projects have no address anywhere and are listed on the
page under "Not on the map". Street lines are on either `Addr1` or `Addr2` in SL — `pick_street`
takes the one with a house number; PO boxes are ignored.

## Where a 010 sales order's location comes from
The same geo step also places every CNET sales order (`sales_cnetdocument`, doc_type `sales_order`):
1. **CNET ship-to** (`ship_addr1/city/state/zip`, trailing suites/units stripped by `strip_unit`;
   will-call to Pace's own warehouse skipped). Source `ship`.
2. Customer street address (`geo_customeraddress`), else 3. customer city. Sources `customer` / `city`.

Result `geo_salesorderlocation`, one row per located document with the normalised `address_key` the
map groups on; rebuilt in full every run, no manual pins (fix the address in CNET). First run
2026-09-01: 46,970 of 50,170 orders located (3,570 new addresses; 509 still waiting on the per-run
Nominatim cap and picked up by later refreshes).

## The page
- **Payload** `GET /map/data/?w=<window>` (`apps/analytics/project_map.py`): projects overlapping
  the window (had PTT hours in it, or created before its end and not closed before its start; all
  time = everything) with location + attributes, the scoreboard (SL sold / to-date figures plus the
  latest `analytics_projectprediction` EAC: revenue, cost, GP, hours at completion, margin change vs
  sold, risk), hours by month (`hm`), crew in the window (top 14 + count), the people who logged
  hours (with their projects in date order), and the unlocated list.
  Windows: `week` · `month` · `90d` (default) · `year` · `all` · `YYYY-MM-DD..YYYY-MM-DD`. Dollar
  margin fields (budgets, GP, EAC dollars, risk) only with `margins.view`; hours and % complete for
  anyone who sees the project; Division-Manager scoping via `acc.allowed_divisions`.
- **010 layer** (with `sales010.view`): `sales_sites()` adds one entry per customer × ship-to place
  (`kind: "sale"`, division `010`) for the CNET SO1 sales orders **booked in the window** (order date
  = `ordered_at`, cancelled excluded): bookings (`cv`), invoiced (`bill`, SL shippers), shipped cost,
  open-backlog count/$ (SL status `O`), bookings by month, salespeople, and the 12 largest orders
  with links to `/sales/010/orders/<n>/`. Realized GP / quoted GM only with margins. 010 sites ignore
  the lifecycle chips (they are not projects) and follow the 010 legend chip; they have no hours, so
  crew/person filters hide them. In the timeline their height is bookings accumulated so far (own
  scale); otherwise they share the projects' dollar scale.
- **Interface** (`project_map.html`, `project_map.css`): full map canvas, a compact floating explorer
  with **Sites / People / Filters** tabs, summary strip, division buttons and a bottom playback bar.
  Context buildings and labels are automatic. Site cards show project/customer, contract value
  (010: bookings), activity and completion; search matches project number, title, customer or address.
  Enter in search fits results (or opens the only match); `/` focuses search; Escape dismisses detail.
  The list supports value/activity/name sorting, optional **In view**, incremental Show more, hover
  highlighting and click-to-fly. Secondary filters and missing locations live in the Filters tab.
- **Architecture** (`project_map_towers.js`): broad, rectilinear buildings with level roof decks,
  restrained glass façades and regular window bays. No tapered crowns, stepped spires or needles.
  A MapLibre custom WebGL layer animates building heights; solid footprint extrusions remain available
  if that renderer fails. Day/night, selection lighting and forgiving silhouette picking are retained.
- **Site totals and scale**: colocated jobs share a full-width building instead of being squeezed into
  skinny campus markers. Hover shows the total; opening the building lists every underlying job.
  Height and width use the selected metric aggregated at the address, a 95th-percentile reference,
  and an uncapped power curve so large totals remain distinguishable. Regional zoom compensation
  keeps buildings readable farther out. At zoom 13.6 and above, world dimensions stop shrinking;
  unmatched buildings have a 48 m height floor and a nominal width floor of 36 m before shoreline clipping. Matched footprints preserve their
  physical dimensions and analytic height is never below mapped roof height + 24 m. Height still
  represents activity/value, not a survey of the physical building. Crew-size mode explicitly sums
  project crew counts; a person working multiple jobs can be counted more than once.
- **Actual buildings** (`project_map_geometry.js`): precise/manual locations match available vector
  geometry inside or within 18 m of the recorded point. OpenFreeMap can batch thousands of separate
  buildings into one MultiPolygon feature, so components are split and deduplicated before matching.
  Only the matching polygon is highlighted, never the whole parent feature or unrelated neighbors.
  A separate GeoJSON layer highlights the real footprint at neighborhood zoom. From zoom 15, the
  project building adopts that actual outline, including concave shapes and courtyards, with an
  analytic height. Multiple addresses matching the same polygon share one physical building.
  Approximate/city-only geocodes retain symbolic blocks. This does not infer the identity of a campus
  building from a project title or repair a source address that points to an office instead of the site.
- **Shoreline**: regional block footprints are clipped against loaded water polygons. Only the land
  component containing the recorded location is retained, so a river never creates a second building
  on the opposite bank. Recorded coordinates continue to drive navigation, routes and provenance.
  Footprints stay fixed on their parcel during camera motion; height compensates smoothly with zoom.
- **Motion and picking**: GPU height interpolation on load, metric changes and replay; no perpetual
  animation at rest. Whole projected silhouettes have 14 px hover / 18 px click tolerance. Direct
  hits beat nearby misses, with depth ordering for overlaps. Empty results clear picking geometry.
  Orbit is opt-in and stops on drag, wheel, Escape, camera presets or tab hiding. Reduced motion
  disables growth, camera flights and orbit. Browser-vendored Earcut 2.2.4 triangulates footprint roofs
  (including holes); polygon-clipping 0.15.7 clips land parcels. Their licenses accompany the assets.
- **Crew**: per-site activity indicators and hours; the People tab selects a person's sites and draws
  their site sequence in first-work order. This is a sequence, not a reconstructed travel path. Monthly
  replay never fabricates headcounts from full-window counts.
- **Timeline**: Play / Pause, scrub, ½× / 1× / 2× and Full window. Project tower size becomes accumulated
  hours; 010 size becomes accumulated bookings on a separate scale. Both use fixed full-window
  reference scales, so accumulation grows consistently. The banner shows the selected month's hours
  and bookings. Changing the time window stops and clears playback. Fetches are aborted
  and sequenced so an older request cannot overwrite a newer window; a failed request keeps the prior
  window and provides Retry. Summary hours use the replay month; People remains labeled "in window".
- **Filters**: division buttons toggle their division; Shift/Alt-click isolates one (repeat restores all).
  The active-filter chips can be cleared individually. Lifecycle defaults to In progress; clearing the
  status chip includes all statuses. An explicitly empty `states=` survives reload. 010 sites continue
  to ignore project lifecycle, but follow division and relevant attribute filters.
- **Views**: Chicagoland, The Loop, Fit sites, reversible 2D/3D, day/night and fullscreen. The explorer
  minimizes to its header; on narrower screens a detail panel hides it temporarily so the selected
  location remains visible. On phone-sized screens the app navigation becomes a dismissible overlay,
  leaving the map and detail panel the full viewport width.
- **Persistence**: window, divisions, statuses, search, attribute filters, metric, color and basemap
  remain in the URL and are mirrored with `PCA.rememberFilters()`. Explorer visibility, list sorting
  and In view use `PCA.pref` / `PCA.setPref`. Shared asset cache-busting includes the map CSS/JS/helpers.
- **Detail panel — project**: a one-glance health line (forecast or final GP and pts vs sold, PM %
  complete, budget hours used → % at completion, over/under-billing, risk), the same Sold → To date →
  At completion scoreboard as the project page (revenue, direct cost, GP, GP %, labor hours), billing /
  cost / hours progress bars with the earned-revenue and budget markers, PM and crew names linked to
  their person pages, dates, hours (lifetime, last 30 days, not yet posted in SL), location provenance +
  fix control, links to the project and customer pages. **010 site**: customer (linked), site, booked /
  invoiced / open backlog / shipped cost / realized GP / quoted GM, salespeople, the largest orders
  (linked, "open" tagged), location.

## Caveats
- A ship-to is where the order shipped — usually the site, sometimes the GC's office; customer
  addresses are the customer's office. The quality tag says which; fix in place when it matters.
- PTT hours are the only "where people are" signal; office/shop time is not on the map.
- Tiles and geocoders are the only internet calls the app makes; everything else is local.
- 010 towers are where the hardware *shipped* (often the customer's receiving dock or IT office);
  blank CNET ship-tos fall back to the customer's address, so one customer can have two towers.
  Bookings are CNET order totals by order date — they tie to the 010 overview's bookings only up to
  cancelled and unlocated orders.
- Tests: `tests/unit/test_project_map.py` (12 address, geocoder, window and sales grouping tests);
  `node --test tests/js/project_map_geometry.test.cjs` (12 geometry tests, including batched-building
  isolation, duplicated tiles, street-scale floors through zoom 22, proportional regional scaling,
  value ordering without a top-size clamp, shoreline clipping and courtyard triangulation).
- Browser verification (2026-09-08 Chicago architecture revision): default regional view, DePaul
  footprint matching, exact footprint reuse and unchanged physical dimensions between zooms 16 and
  19; grouped job access; shoreline clipping; metric/timeline changes; day/night; 3D fallback and
  reduced motion. The data endpoint and permission gates are unchanged by this renderer revision.
