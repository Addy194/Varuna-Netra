# Varuna Netra (formerly SentinelMar) — Maritime Oil-Spill Detection & Vessel Correlation (PRD)

## Original problem statement
Web-based decision-support system for maritime authorities that ingests satellite-derived spill observations and AIS tracks, detects spatial-temporal overlap, and produces auditable ranked vessel candidates — not legal conclusions. Provider-neutral ingestion (Sentinel-1 first), normalized spill/AIS schemas, configurable corridor/time-window correlation, drift-back uncertainty (wind/current or degraded), transparent scoring (spatial, time gap, track continuity, heading, drift plausibility, AIS reliability), evidence bundles, GeoJSON, confidence bands, processing logs, analyst review states, alerts, immutable decisions. Statuses: possible / probable / insufficient_evidence / analyst_confirmed / indeterminate. Reproducible, auditable results.

## User choices
- No auth for MVP (open API) · Leaflet + OSM tiles · external polygons + mock detector · seeded demo data · in-process asyncio job queue with MongoDB `jobs` collection + polling.

## Architecture
- Backend `/app/backend`: FastAPI (`server.py`), `db.py` (Motor, indexes: 2dsphere on AIS/spill/scene, time + unique dedup), `models.py` (Pydantic contracts + vocabularies), `geo.py` (shapely validation, haversine, drift helpers), `correlation.py` (`corr-1.0.0` deterministic scoring engine), `services.py` (ingestion, dedup/quality checks, mock detector, correlate job handler + alerting), `jobs.py` (async queue, retries ×3, job logs), `seed.py`, `routers/{ingest,cases,system}.py`.
- Frontend `/app/frontend/src`: React 19 + react-leaflet 5; pages Dashboard, CaseDetail (map + candidates + review + evidence + log), Ingest, Jobs & Alerts.
- Collections: scenes, spill_observations (raw_input preserved), cases (mutable state), correlation_results (immutable, versioned, input_hash), reviews (immutable), audit_events (separate), jobs, alerts, ais_positions.

## API (all under /api)
POST/GET scenes, POST scenes/{id}/detect (mock), POST/GET spill-observations, POST ais/positions (dedup + flags), GET ais/positions, GET ais/vessels, GET cases, GET cases/{id}, POST cases/{id}/correlate (202 job; sync=true option), GET cases/{id}/candidates, POST cases/{id}/review, GET cases/{id}/reviews, GET cases/{id}/evidence, GET cases/{id}/geojson, GET jobs, GET jobs/{id}, GET alerts, POST alerts/{id}/ack, GET audit, GET config/defaults, GET stats, POST seed.

## Implemented (2026-06 — MVP)
- Phases 1–4 of the stated plan: schemas/vocabulary, ingestion + validation + dedup + audit, baseline correlation with explainable factors and caps (severe spill flags, low detection confidence, low AIS reliability, multiple-vessel ambiguity, post-acquisition-only tracks), drift back-projection (3% wind + current) or `degraded`, GeoJSON export, high-confidence alerts, immutable analyst review preserving prior automated results, mock SAR detector as replaceable module.
- Tested: iteration_1 — 20/20 backend tests pass; frontend flows verified. Fixed testid forwarding on map, fragment keys.

## Implemented (iteration 2)
- **Authority accounts**: JWT (PyJWT, bcrypt) email/password auth, roles analyst < supervisor < admin (`auth.py`, `routers/auth.py`). All `/api/*` endpoints protected; actor identity (email/role) written on every audit event, review, job, alert ack and export. Brute-force lockout (5 fails / 15 min, X-Forwarded-For aware). Admin user management (`/api/users` CRUD, `/users` page). Supervisor-only: alert ack, `POST /cases/{id}/override`. Seeded accounts in `/app/memory/test_credentials.md`; admin = workspace owner email.
- **Evidence PDF**: `GET /api/cases/{id}/evidence.pdf` (reportlab + matplotlib map snapshot; case summary, sources, environment, ranked candidates + factor tables, processing log, versions, decisions, audit). Audited as `evidence.exported`.
- **Live weather drift**: `weather.py` Open-Meteo (ERA5 archive / forecast wind 10 m; Copernicus Marine surface current). `POST /cases/{id}/environment/fetch` persists wind/current + provenance on the spill observation; `correlate` accepts `fetch_environment: true`. UI: "Live weather" button + environment summary + param checkbox.
- **Time scrubber**: map slider/play replaying AIS tracks (interpolated heads, AIS-gap dashed markers, spill dimmed before satellite pass); GeoJSON tracks now carry timestamps/sog/cog.
- Tested: iteration_2 — 16/16 backend, all frontend flows pass. Fixed lockout identifier, scrubber setState warning, users loading state.

## Implemented (iteration 3)
- **Password reset**: `POST /auth/forgot-password` (generic response, no enumeration) → single-use sha256-hashed token, 60 min expiry; `POST /auth/reset-password`; Resend email via `emailer.py` (RESEND_API_KEY empty → link logged server-side and shown to admins at `GET /auth/reset-requests` / Users page "copy link"). Clears lockouts on reset. Pages `/forgot-password`, `/reset-password`.
- **CSV AIS upload**: `csv_ingest.py` header alias auto-detection, delimiter sniffing, timestamp parsing (ISO/epoch/common formats); `POST /ais/csv/preview` + `POST /ais/csv/ingest` (multipart, optional mapping JSON, row-level errors, dedup via existing pipeline). Drag-and-drop `CsvUpload` component with mapping selects on Ingestion page.
- **Jurisdiction zones**: `jurisdictions` collection (2dsphere), seeded simplified North Sea EEZs + Rotterdam port-state box (demo, not official); admin CRUD `/jurisdictions`, `/jurisdictions/geojson`, `resolve-all`, `/cases/{id}/jurisdiction/resolve`. Cases get `jurisdictions[]` + `primary_jurisdiction` (centroid containment, port_state > territorial > eez) on creation; shown on dashboard, case chip, case map layer, PDF. `/zones` page with map + admin form.
- **Vessel history**: `GET /vessels/{mmsi}/profile` (appearances from latest result per case, decisions naming the vessel, AIS coverage summary, disclaimer); `/vessels/:mmsi` page linked from candidate rows and vessel list.
- Tested: iteration_3 — 23/23 backend, all frontend flows pass, no issues.

## Implemented (iteration 4)
- **Official EEZ import**: `marine_regions.py` fetches Marine Regions WFS (eez by ISO3), repairs geometry (make_valid/unary_union/buffer(0), orient retry) for 2dsphere; `POST /jurisdictions/import-eez` (admin, 202 job) → zones flagged `official:true` + `mrgid`; re-resolves all cases. Zones page import panel + source labels.
- **Email settings**: admin-only `GET/PUT /auth/email-settings`, `POST /auth/email-settings/test`; DB settings override env; key masked. Users page `EmailSettings` panel (badge CONFIGURED / NOT CONFIGURED). **Real delivery NOT configured** — no Resend key supplied; forgot-password falls back to logged links.
- **Watchlist**: `routers/watchlist.py` (list any role; supervisor+ add/remove, severity whitelist, dup check); correlation enriches candidates with `watchlist{reason,severity}` and raises `watchlist_hit` alerts. `/watchlist` page, candidate badges, vessel-profile action.
- **Case timeline & sharing**: `routers/timeline.py` — `GET /cases/{id}/timeline` (JSON) + `/timeline.html`; supervisor+ `POST /cases/{id}/share` (sha256 token, 1–720 h expiry, note) → public `GET /api/share/{token}` read-only HTML (uses FRONTEND_URL), view counter, revoke. `CaseTimeline` tab in case detail.
- Tested: iteration_4 — 33/33 backend, all frontend flows pass, no issues.

## Implemented (iteration 5)
- **Scene imagery & evidence files**: `storage.py` (Emergent Object Storage, `EMERGENT_LLM_KEY`), chunked upload `POST /uploads/init` → `PUT /uploads/{id}/chunks/{i}` → `POST /uploads/{id}/complete` (≤50 MB; png/jpg/webp/tif/pdf/csv/txt/json/geojson; kinds sar_scene…other); `GET /cases/{id}/attachments`, `GET /attachments/{id}/download`, supervisor soft-delete. Images embedded in evidence PDF (section 8), attachments listed on timeline (JSON/HTML/share). Case detail "Files" tab (`Attachments.jsx`).
- **Alert email notifications**: `notifications.py` — every alert (high-confidence, watchlist, zone rule) emails active supervisors+admins (per-user `notify_alerts` opt-out on Users page) plus admin `alert_recipients`; `alerts_enabled` toggle in Email Settings. Outcome stored on `alert.notification` + `notifications` collection + audit `alert.notified`. **Delivery NOT configured** (no Resend key) → status `not_configured`.
- **Case comparison**: `GET /cases/compare/{a}/{b}` (geojson + candidates + shared_vessels); `/compare` page with split / overlay (side-tinted) maps, shared-vessel table, "Compare" button on case detail.
- **Zone alert rules**: `rules.py` + `routers/rules.py` — supervisor CRUD `/zone-rules` (zone_code, optional min_area_km2 / min_confidence, severity, primary_only, note), evaluated on case open, after correlation, and `POST /zone-rules/evaluate`; dedup per case+rule; `ZoneRules.jsx` panel on Zones page.
- Tested: iteration_5 — 29/29 backend, all frontend flows pass, no issues.

## Implemented (iteration 6)
- **Global satellite imagery**: `satellite.py` — Microsoft Planetary Computer STAC (open, no key): `GET /satellite/collections`, `POST /satellite/search` (bbox/date/collection, Sentinel-1 GRD + Sentinel-2 L2A w/ cloud filter), `GET /satellite/preview` (proxied quicklook, retry + cache + thumbnail fallback), `POST /satellite/register` (→ SentinelMar scene with STAC href/metadata; optional mock detect). NASA GIBS daily true-colour basemap (`GibsLayer.jsx`) on Scene Explorer and case map ("Satellite" toggle, acquisition date).
- **Scene Explorer** `/explorer`: world map with region presets, search current view, footprints + SAR/optical previews, register / register+mock-detect.
- **Live global AIS**: `ais_live.py` aisstream.io websocket collector (bboxes, PositionReport → existing dedup pipeline), `GET /ais/live/status`, admin `PUT /ais/live/settings`; `LiveAis` panel on Ingestion. **Key NOT provided → not configured.**
- Mock detector kept as labelled placeholder.
- Tested: iteration_6 — 19/19 backend, all frontend flows pass; preview retry/cache added after review.

## Implemented (iteration 7)
- **Tile performance**: lazy/visible-first tiles (`updateWhenIdle`, `keepBuffer:0`) on all maps + GIBS; Explorer previews load only when cards scroll into view; quicklooks cached in object storage (`scene.quicklook_path`).
- **Auto Scene Watch**: `scene_watches` (bbox, collection, auto_detect, last_polled); `.emergent/crons.yml` every 3 h → `POST /cron/scene-watch` (Bearer `WEBHOOK_CRON_SECRET`, idempotent on X-Webhook-Id) enqueues `scene_watch_poll` job; registers new passes, runs detector, raises `new_scene` alerts + email. Supervisor CRUD + "Poll now"; Explorer sidebar panel "Watch this view".
- **AIS density heatmap**: `GET /ais/density` server-side grid aggregation (zoom-sized bins, 60 s cache) → leaflet.heat layer on Explorer (24 h / 7 d / 90 d). *(Substituted for PostGIS/H3/MVT/Deck.gl to stay on the Mongo + Leaflet stack.)*
- **Scene overlay**: `GET /scenes/{id}/quicklook` + `/overlay` (EPSG:4326 bbox bounds) → Leaflet ImageOverlay on case map with toggle + opacity slider.
- **Dark-spot detector (EXPERIMENTAL)**: `detector.py` — OpenCV Gaussian + Otsu threshold on S1 VV quicklook, morphology, contour elongation ≥2.2, pixel→lon/lat affine via scene bbox; confidence ≤0.55, flags `lookalike_suspect`+`experimental_detector`; WebP crop thumbnail attached to each case. Replaces mock for STAC scenes (`detect_scene`); mock kept for imagery-less scenes.
- **Focus spill**: turf bbox of spill + top candidate, `flyToBounds`; marker label "TOP CANDIDATE — not confirmed" vs "RESPONSIBLE (analyst confirmed)" only when review_state confirmed for that MMSI.
- **Before/After**: `GET /cases/{id}/before-after` (STAC nearest scene before/after, S1 or S2, ±N days) → two synchronised Leaflet maps with ImageOverlay + spill polygon.
- **Events gallery** `/events`: `GET /spill-events` (offset/limit, compound indexes, filters start/end/min_conf/status/source/jurisdiction, thumb path) → react-window virtualized grid, URL-driven filters.
- Tested: iteration_7 — 20/20 backend, all frontend flows pass. Known cosmetic: console warning "<option> cannot be a child of <span>" (source not found in app code; likely injected/extension).

## Implemented (iteration 8)
- **Detector feedback**: `POST/GET /cases/{id}/detector-feedback` (TP/FP/uncertain + FP reason enum, versioned, audited, immutable), `GET /detector/precision` (TP/(TP+FP) per detector version, FP-reason breakdown, weekly, pending); Dashboard `DetectorPrecision` card (recharts donut); panel on case Review tab; PDF section under analyst decisions.
- **Live alert feed**: `events.py` in-process SSE broadcaster; `GET /alerts/stream?token=` (hello/alert/job events, keepalive), `GET /alerts/latest` polling fallback; `LiveFeedProvider` (EventSource → toast, header bell + unread count, critical red pulsing banner + Web-Audio siren for high severity, mute, auto-fallback to 10 s polling). Dashboard/case detail refresh on job completion. 10-minute inactivity auto-logout (`InactivityGuard`).
- **AIS gap filling** `gapfill.py`: gaps > `gap_threshold_min` (30) dead-reckoned from last SOG/COG blended to next fix (10-min synthetic points, `interpolated` flag); kinematically impossible transits (vs vessel-type max speed) → `spoof_suspect`, not interpolated; continuity uses real fixes only; spatial penalty ∝ gap length when closest approach is interpolated; dashed segments on map + tooltip; `params.fill_gaps` (default on).
- **Drift back-model** `drift.py` (lagrangian-backtrack-0.1.0): hourly reverse steps ≤72 h (3% wind + current), σ(t) from eddy diffusivity + 35% velocity uncertainty, 2σ origin envelope + most-likely window (from spill age); drift factor = exp(−z²/2) against envelope; layers `drift_envelope`/`drift_likely`/`drift_path` on map; PDF explains. Algorithm version **corr-1.1.0**.
- Tested: iteration_8 — 15/15 backend, all frontend flows pass.

## Implemented (iteration 9)
- **Petroleum asset search**: seeded gazetteer (~70: Indian basins/fields/ports, global fields, terminals, lanes, countries) + live EEZ zones; rapidfuzz search w/ name-substring boost (`GET /gazetteer/search`), admin CRUD; `AssetSearch` fly-to + footprint on Explorer and case map.
- **Historical spill archive**: 12 seeded precedents (`historical_spills`), `/archive` page (URL search), supervisor add/delete; `GET /cases/{id}/precedents` similarity (0.5 distance + 0.3 log-volume + 0.2 oil type) → Precedents drawer on Response tab.
- **Remediation playbook** (`playbook.py`, ADVISORY): volume by thickness class, coast distance via global-land-mask, depth class, sea state, drift → tactical boom/skimmer coords at down-drift edge; Tier 1/2/3 with dispersant/ISB/bioremediation suitability rules; Response tab + PDF section 9.
- **Prosecution export**: supervisor+ ZIP (PDF, case/spill/scene/AIS/results/reviews/audit/feedback/playbook JSON, attachments, MANIFEST.json SHA-256 + content hash), ledger `prosecution_exports`, audit; public `GET /verify/{hash}`, `POST /verify` (re-hash every file, detect tampering/repackaging), `/verify` page.
- Tested: iteration_9 — 28/29 backend (fuzzy ranking fixed after), all UI flows pass (testing agent fixed missing hasRole import in CaseDetail).

## Implemented (iteration 10)
- **Indian maritime territories**: Marine Regions import generalised to layers `eez` / `eez_24nm` / `eez_12nm` → zones IND-EEZ (eez), IND-CZ (contiguous), IND-TS (territorial); 12/24 NM bands keep inner rings; priority territorial < contiguous < eez; `zone_label` on jurisdictions. Case map: distinct styles (solid orange TS, dashed amber CZ, dotted blue EEZ) with per-kind toggles; Zones page labels + layer checkboxes + "India · all 3 zones" preset. Detection details show zone name (case chip, "also" chips); candidates tagged with `zone`/`zones` from closest AIS fix (`resolve_point_zones`).
- Tested: iteration_10 — 10/10 backend, all UI flows pass.

## Implemented (iteration 11 — code-quality pass)
- Login demo passwords removed from bundle → `REACT_APP_DEMO_PASSWORDS="analyst:…,supervisor:…"` in gitignored `frontend/.env` (quoted, `#` safe); buttons pre-fill email only when unset.
- Backend test credentials moved to `backend/tests/.env.test` (gitignored) via `tests/conftest.py` + `TEST_*_PASSWORD` env vars.
- Frontend: flat `eslint.config.js` (react + react-hooks) + `yarn lint`; 0 errors / 0 app warnings. Loaders wrapped in `useCallback` (CaseTimeline, DetectorFeedback, Archive, VesselProfile), context values memoised (AuthContext, LiveFeed), index keys → stable keys (Jobs, CsvUpload, CaseTimeline, CandidatesTable, DetectorPrecision), Recharts `minWidth/minHeight` fix, dead imports removed. pyflakes: no undefined names.
- Skipped by user decision: httpOnly-cookie auth, big refactors (run_correlation/build_pdf/build_playbook/CaseMap), `random`→`secrets` (deterministic seeded mock), lazy imports.
- Verified: 26/26 backend (iter 2 + 10 suites), login/dashboard/vessel-profile smoke screenshot.

## Implemented (iteration 11b — second review pass)
- Empty catch blocks → `console.warn` with context (LiveFeed ×2, AuthContext); EventSource cleanup nullifies ref.
- `CaseMap.jsx`: all inline `style`/`pathOptions` hoisted to module constants or pure helper fns; `colorFor`/`selectHandler`/`zoneFilter` in `useCallback`. `ZoneRules.jsx`: `activeZones`/`activeRules` via `useMemo`.
- Type hints on `db.py`, `jobs.py`, `gapfill.py`, `correlation_env.py`.
- Complexity trims (behaviour byte-identical vs HEAD, verified by running old/new side-by-side): `csv_ingest._row_to_position`, `marine_regions._fetch_or_fail/_zone_doc/_store_zone`, `detector._dark_mask/_contour_to_spot`, `gapfill._dead_reckon`.
- Tested: iteration_11 — backend 3/3 + 99/101 regression (2 known flaky: non-idempotent CSV dedup fixture; login lockout under parallel runs — passes alone), all UI flows pass, single SSE connection across navigation.
- Known cosmetic: "<span> cannot be a child of <option>" console warning (pre-existing, source not in app code).

## Implemented (iteration 12)
- **Cookie sessions**: JWT only in `access_token` httpOnly/Secure/SameSite=Lax cookie (set on login, cleared on logout); axios `withCredentials`; nothing in localStorage; SSE `/alerts/stream` authenticates via cookie (`?token=` kept for programmatic clients); Bearer header still accepted. Preview ingress rewrites SameSite→None+Partitioned (platform behaviour).
- **Correlation pipeline**: `correlation.py` split into `load_inputs → build_corridor → fill_and_filter_tracks → score_candidates(score_vessel + per-factor helpers) → rank_and_status → assemble_result` over a `Context` dataclass. Golden-master: `scripts/freeze_correlation_golden.py` froze 9 seeded runs → `tests/fixtures/correlation_golden.json`; `tests/test_correlation_golden.py` asserts byte-identical output (9/9).
- **ICG alert routing** (`icg.py`, `routers/icg.py`): 14 APPROXIMATE district sea-boxes across 5 regions (NW/W/E/NE/A&N), flagged `approximate`, admin PUT to replace with official geometry, `resolve-all`; cases get `icg`, all alert kinds carry `icg`, email subject/body include routing; case header chip, alert lines (Dashboard/Jobs), Zones page layer + admin panel (`IcgDistricts.jsx`).
- **Shoreline vulnerability** (`vulnerability.py`): forward Lagrangian track 72 h + 24/48/72 h 2σ envelopes; 34 curated Indian sensitive sites seeded (`sensitive_sites`); ETA = first hour plume disc reaches site; priority = sensitivity × urgency × proximity; degraded (no forcing) → 60 km radius by distance. OSM Overpass enrichment as background job cached in `case_vulnerability_osm` (mirrors tried; **unreachable from preview pod → status failed, curated still shown**). Case tab "Vulnerability" (`Vulnerability.jsx`) with map + ranked table + enrich button; PDF section 10.

## Implemented (iteration 13 — deployment readiness)
- deployment_agent: PASS (no findings). Fixed: `.gitignore` no longer ignores `.env` files (platform requirement); vessel-profile queries capped (5000 fixes / 500 results / capped `$in`, `fixes_analysed` reported); candidates watchlist lookup scoped by `$in` candidate MMSIs.
- Tested: iteration_13 regression on vessel profile + candidates watchlist badge.

## Implemented (iteration 14)
- **Dark-vessel detection (EXPERIMENTAL)** `dark_vessel.py`: CA-CFAR bright-target search (15×15 window, 3×3 guard, 4σ) on the Sentinel-1 quicklook → compact blobs; AIS cross-check ±30 min within 3 km; unmatched → `dark_candidate` with dead-reckoned escape cue (slick major axis away from spill, 12 kn, 1–6 h). `POST /cases/{id}/dark-vessels/scan?radius_km`, `GET /cases/{id}/dark-vessels`; scans in `dark_vessel_scans`, summary on `case.dark_vessels`, audited. UI panel under Candidates + red boxes/dashed trajectories on case map. Needs real SAR quicklook (400 otherwise).
- **Evidence Vault** `vault.py` + `GET /archive/{id}/vault`: structured legal/ecological evidence (ruling, penalties, cleanup cost, compensation, ecological impact, source links) for the 12 seeded spills; RECONSTRUCTED day-by-day footprint frames (√t spread then weathering). Page `/archive/:id` with mini-map replay slider/play, GIBS optical before/after toggle, evidence side panel.
- **District alert recipients**: `icg_districts.recipients[]` via admin PUT; `recipients_for_alerts(icg_code)` puts district desk emails first; Zones admin edit form field. **Email delivery still NOT CONFIGURED** (no Resend key/sender supplied) — configure under Users → Email settings.

## Implemented (iteration 15 — code-quality pass 3, pure refactor)
- `report.build_pdf` → `_Doc` styles/table helper + `_sec_header/_sec_summary/_sec_observation/_sec_map/_sec_calculations/_sec_versions/_sec_reviews/_sec_audit/_sec_attachments/_sec_playbook/_sec_vulnerability`; `playbook.build_playbook` → `_situation/_tactical/_tier1/_tier2/_tier3`; `dark_vessel.scan_case` → `_scene_with_imagery/_ais_around/_classify_target/_escape_heading`.
- Verified byte-identical with `scripts/refactor_baseline.py` (capture before / compare after): 60 playbooks + 6 PDF texts IDENTICAL; correlation golden 9/9.
- `CaseMap.jsx` render split into `DarkVesselLayer`, `DriftLayers`, `VesselTracks`, `ClosestFixes`; static pathOptions hoisted in `EvidenceVault.jsx`/`Vulnerability.jsx`.
- Report items re-confirmed as false positives: `tests/test_iteration3.py` secret (already env-based), `is` literal comparisons (all `is None`), `random` (deterministic seeded mock), lazy imports (own modules), hook deps (eslint react-hooks clean).

## Implemented (iteration 16 — rebrand)
- Product renamed **SentinelMar → Varuna Netra** everywhere user-facing: header/login logo, browser title, API title, PDF author/footer, email subjects, timeline share footer, verify page, prosecution messages, OSM User-Agent, ICG seed source. Internal identifiers intentionally kept: demo emails `@sentinelmar.demo`, `APP_NAME` storage prefix, `sentinelmar:unauthorized` DOM event, test file names.

## Implemented (iteration 17 — production deploy fix)
- Root cause of k8s readiness timeout: no root `/health` route (only `/api/*`), lifespan blocked on Atlas index creation + 7 seeders before uvicorn bound, and matplotlib/cv2/global_land_mask imported at process start (font-cache build) → probe refused → restart loop.
- Fix: `GET /health` + `GET /api/health` (`{status, ready}`); seeds/indexes run in a background task after bind (`app.state.ready`); lazy imports (`report.render_map_png`, `playbook._globe`, `lazy_libs.cv2` proxy), `MPLCONFIGDIR=/tmp/mplconfig`. `import server` 0.8s. Also `.gitignore` `.env` lines had been re-added by an auto commit — removed again (deployment_agent PASS).
- Note: `scripts/refactor_baseline.py compare` now shows expected data-driven diffs (brand rename in PDF text; live weather fetched on some cases) — re-run `capture` before the next pure refactor.

## Implemented (iteration 18 — LIVE mode, demo retired)
- `livemode.py` + `routers/realtime.py`: `DEMO_MODE` env + `settings.data_mode.demo_purged` gate demo seeding; admin `POST /system/purge-demo` (removed all seeded North Sea + test-artefact scenes/spills/cases/results/AIS); 9 real Indian Sentinel-1 watch regions seeded with auto-detect (`seed_india_watches`), `POST /scene-watches/ingest-now?days=N`; real `GET /system/health` (Mongo ping, STAC GET, AIS worker, OpenCV, last scene, 24 h counters); `GET /ais/status` per spec (no silent demo fallback: connected=false, reason='API key not configured'); `GET /cases/{id}/provenance` with badges REAL SENTINEL-1 / EXPERIMENTAL / MOCK / LIVE / HISTORICAL / NONE / DEMO. AIS default bbox → Indian EEZ.
- Frontend: `/health` "Data Sources" page (SystemHealth.jsx: live cards, LIVE/DEMO badge, AIS-unavailable banner, Ingest-7-days + Purge buttons), `Provenance.jsx` panel on every case.
- Result on 2026-09-10: 45 real Sentinel-1 scenes over India ingested, 120 experimental detections/cases, 0 demo cases. AIS remains unavailable until `AISSTREAM_API_KEY` is supplied (Users → Live AIS or env).
- Test credentials unchanged; seeded demo *cases* are gone, so older test suites referencing `SPL-20260610-*` are obsolete.

## Implemented (iteration 19 — real AISStream integration)
- ROOT CAUSE of "[[10,-5],[20,5]]" + offline: legacy `settings.ais_live` doc (written by the old admin panel `LiveAis.jsx` region dropdown, 2026-09-07) held a Gulf-of-Guinea bbox with `enabled=false` and no key; worker also required a DB-stored key. Legacy doc deleted; key now ONLY from env `AISSTREAM_API_KEY` (never in DB/API/frontend).
- `ais_live.py` rewritten: one worker/process, backoff 1→30 s, subscription `{APIKey, BoundingBoxes, FilterMessageTypes[5]}`, SubscriptionConfirmation/first-frame → `subscription_confirmed`, binary frames, PositionReport + Class B parsing with coordinate validation, active cache (30 min stale), `status()` runtime-only (msg/min, reason tri-state), `test_connection()`. Coverage single source `settings.ais_coverage` (mode default|manual|spill; bbox [S,W,N,E] → AISStream [[lat,lon],[lat,lon]]); 4 Indian regional defaults; `coverage_for_spill` auto-follows new real spills (+100 km) unless manual. Routes: `/ais/status`, `/ais/coverage`, `/ais/coverage/region/{r}`, `/ais/vessels`, `/ais/tracks/{mmsi}`, `/ais/test-connection`, `/ais/debug/aoi`. 13 unit tests in `tests/test_ais_live.py`. `.env.example` added.
- UI: /health AIS card tri-state + msg/min + coverage + region quick-select; Ingest `LiveAis.jsx` rewritten (no key input; env only).
- NOT VERIFIED LIVE: no `AISSTREAM_API_KEY` in this environment (also none ever committed to git). wss endpoint reachable (TLS OK; server closes on invalid key).

## Implemented (iteration 20 — stabilization: canonical AIS endpoint, real-SAR dark-vessel chain, test hygiene)
- **Duplicate routes removed**: `routers/ingest.py` `GET /ais/vessels` (array; shadowed) and `routers/realtime.py` `GET /ais/status` (shadowed). Canonical: `routers/ais_live.py` — `GET /ais/vessels` → `{source:"AISStream", mode:"live", state, configured, connected, count, stale_after_min, vessels[] (live cache), indexed_count, indexed[] (per-MMSI history from ais_positions incl. sources)}`; `GET /ais/status` (+ alias `/ais/live/status`) now includes `state` ∈ NOT_CONFIGURED|CONNECTING|CONNECTED|LIVE|RECONNECTING|OFFLINE. Ingest.jsx consumes `data.indexed`; LiveAis/SystemHealth badges keyed on `state`.
- **Sentinel SAR vs quicklook separation** `sentinel_assets.py`: STAC asset inventory inspected (not assumed) → `analysis_asset` (vv>hh>vh>hv), native preview; Planetary Computer SAS signing server-side just-in-time (never persisted); `sar_bbox_png` renders an AOI window of the REAL GRD COG via PC data API `/item/bbox/...` (no rasterio needed); generated quicklook when no native preview; `resolve_scene_assets` state SAR_READY|QUICKLOOK_GENERATING|SAR_UNAVAILABLE with specific reasons (No Sentinel scene attached / metadata unavailable / SAR asset missing / signing failed / raster download failed / quicklook generation failed / analysis failed). Routes: `GET /sentinel/latest`, `GET /sentinel/search`, `GET /sentinel/scenes/{id}` (internal id or STAC id), `/assets` (HEAD-checks signed SAR), `/quicklook`; `POST /cases/{id}/attach-scene` (explicit or auto: nearest S1 GRD covering spill ±6 h, registers+attaches, audited); `GET /cases/{id}` adds `scene_status`. Dark-vessel scan now runs CFAR on the real VV AOI window (`analysis_input.kind = sar_aoi_window`), falls back to quicklook, auto-attaches scene, exposes `ais_available`/`ais_note` (0 AIS fixes ⇒ "dark" not interpretable). Verified on real scene S1D_…_20260910T003216: 167 bright targets, 3 s.
- Frontend: DarkVessels panel shows GREEN/YELLOW/RED SAR state chip, "Find & attach Sentinel-1 scene" button, specific error text, no-AIS warning; Ingest samples moved from North Sea to Mumbai offshore; `/scenes/{id}/detect` runs the real detector for STAC scenes and returns 409 for mock in LIVE mode (button reads "Detect (SAR)"); unknown routes redirect to `/`.
- Test hygiene: `tests/conftest.py` auto-skips tests whose function/class/fixture source references the purged demo dataset and the legacy pre-auth suite; obsolete `/ais/live/settings` tests replaced (that test wrote the `[[10,-5],[20,5]]` box); restart test opt-in (`ALLOW_BACKEND_RESTART_TEST=1`); `ais_live.stop()` on lifespan shutdown. Purge also removes `TEST_*` scenes and `mock_detector` spills. Full suite: 191 passed / 111 skipped / 0 failed (last run). Frontend `yarn build` OK, ESLint 0 errors.
- Indexes added: `scenes.acquisition_time`, `cases.scene_id`, `dark_vessel_scans(case_id, created_at)`.
- STILL BLOCKED: live AIS — `AISSTREAM_API_KEY` absent in backend env (state NOT_CONFIGURED). Resend email unconfigured.

## Data truthfulness matrix
- REAL: Sentinel-1 GRD scenes/assets (Planetary Computer STAC), AISStream when `state=LIVE`.
- NEAR-REAL-TIME: latest registered Sentinel-1 acquisition (archive, labelled with acquisition age; never "live").
- UNAVAILABLE: AIS when key/network/coverage missing (truthful OFFLINE/NOT_CONFIGURED; no demo substitution).
- EXPERIMENTAL: dark-spot Otsu detector, CFAR dark-vessel heuristic.
- DEMO/MOCK: only when `DEMO_MODE=true` and demo not purged; mock detect returns 409 in LIVE mode.

## Backlog (prioritized)
- P1: Resend + aisstream keys; U-Net SAR segmentation; OpenDrift forward drift; socio-economic vulnerability (Overpass POIs); WhatsApp citizen reports; i18n (Hindi/Tamil/Marathi); outbound port-authority webhooks (HMAC); ErrorBoundary around CaseDetail; exponential lockout backoff.
- P2: Additional met/ocean providers, replay testing harness, observability/metrics, SAR segmentation model once labeled data exists, separate worker process (Celery/Redis).

## Known limitations
- Mock detector is a placeholder (MOCKED by design). Drift model is a simple 3%-wind + current linear back-projection. No encryption/RBAC yet.
