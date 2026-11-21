> **Historical document** — the original phase-by-phase build brief. The current
> system is described in [../architecture.md](../architecture.md); some details below
> (e.g. `app/instance`, severity v1, flat cost table) are superseded.

# Master prompt — Municipal Road Inspection System (Phases 1–14)

Copy everything below into Cursor and run it against **this** repo. Do not invent new table/column/route names. Enhance existing code. Do not rewrite working modules.

---

You are implementing and **testing** the Municipal AI Road Inspection System through **Phase 14**. Tech stack: **Flask 3.x + PostgreSQL 15 + PostGIS 3.3 + SQLAlchemy 2.x + Jinja2 + Bootstrap 5 + Alpine.js + Leaflet.js + ImageKit + Ultralytics YOLO**. App root is `municipal_ai_system/`. Package is `app`. Entry: `run.py`, `wsgi.py`, `start.bat` / `start.sh`.

## Hard constraints

- **ImageKit only** for cloud media (never MinIO/S3/Google Maps/Mapbox/React/Next/Celery/Redis).
- **Do not change PostGIS table structure** unless a column is actually missing.
- Use existing names only:

| Concept | Correct name |
|---------|----------------|
| Inspection PK (Python) | `inspection.id` (DB column `inspection_id`) |
| Human code | `inspection.inspection_code` (e.g. `INS-2026-000001`) |
| Inspector FK | `inspection.user_id` |
| GPS geometry | `inspections.location_geometry` POINT SRID 4326 |
| Lat/lng columns | `location_latitude`, `location_longitude` |
| Media table | `inspection_media` (`media_id` → `media.id`) |
| Media URLs | `file_path`, `cdn_url`, `storage_bucket` |
| Ward name | `Ward.name` (column `ward_name`) |
| Ward polygon | `ward_boundary` |
| Map page | `protected.map_view` → `/map` |
| Upload page | `inspections.new` → `/inspections/new` |
| List | `inspections.list_all` → `/inspections/` |
| Detail | `inspections.detail` |
| Roles | `super_admin`, `municipal_admin`, `field_officer`, `field_worker`, `contractor`, `viewer` |
| Status | `pending_analysis` (see `app/utils/status_constants.py`) |

Templates already exist — **enhance**: `templates/base.html`, `dashboard/*.html`, `inspections/new.html`, `inspections/list.html`, `inspections/detail.html`, `inspections/my_inspections.html`, `map/index.html`, `repairs/*`, `detections/*`. Do not create parallel `dashboard.html` / `map_view.html` filenames that break `url_for`.

## FIRST: fix login “Database is not configured”

Means offline mode (`DATABASE_CONFIGURED=false`), not a wrong password.

1. Create UTF-8 `municipal_ai_system/.env` from `.env.example`.
2. Set real `DATABASE_URL` (Supabase Session pooler) + ImageKit keys. Never commit secrets.
3. `OFFLINE_MODE=false` for live DB login; `true` for campus demo without DB.
4. Restart: `venv\Scripts\python.exe run.py`. Check `GET /health`.
5. Seed if needed: `python scripts/seed_auth.py`.

## Phase checklist (test each; fix only what is broken)

### Phase 1 — Flask app factory
- `app/__init__.py` `create_app()`, `app/config.py`, `app/extensions.py` (db, csrf, limiter, login_manager)
- `/health` JSON, `/` → login or dashboard
- Tests: `python -m pytest tests/test_security_ops.py -q`

### Phase 2 — PostGIS models (20 tables)
- Canonical list: `app/models/tables.py` `TABLE_NAMES`
- Explicit ORM: `User`, `Role`, `Municipality`, `Ward`, `Inspection`, `InspectionMedia`
- Remaining tables: `reflect_models()` in `app/models/reflection.py`
- Geometry: `location_geometry`, `ward_boundary` — GIST indexes already in RDS; do not recreate blindly
- `python scripts/inspect_schema.py` when DB reachable

### Phase 3 — Auth + RBAC
- Blueprint: `app.auth` (`auth_bp`), **not** `app.routes.auth`
- Login: username **or** email, lockout via `AUTH_MAX_FAILED_ATTEMPTS`
- Decorators: `app/decorators.py` `roles_required`
- Role dashboards: `app/routes/dashboard.py` `ROLE_DASHBOARD_MAP`

### Phase 4 — Jinja2 UI + Leaflet
- CDNs: Bootstrap 5, Alpine.js, Chart.js, Leaflet 1.9, leaflet.markercluster
- Map: `templates/map/index.html`, `static/js/map.js`
- APIs: `GET /api/inspections/geojson`, `/api/inspections/radius`, `/api/wards/boundaries`, `/api/analytics/summary`, `/api/map/detections`
- Upload UI: drag-drop, preview, browser GPS + manual lat/lng (`location_latitude`, `location_longitude`)
- Severity colors: SEVERE red, MODERATE orange, MINOR green

### Phase 5 — ImageKit + EXIF GPS
- `app/services/imagekit_service.py`, `gps_service.py`, `video_service.py`, `validation_service.py`, `media_service.py`
- Upload: `POST /inspections/new` and `POST /api/inspections/upload`
- Offline (`OFFLINE_MODE=true` or missing ImageKit keys): local `TEMP_UPLOAD_ROOT`, no ImageKit call
- GPS: EXIF DMS → decimal; fallback form fields; PostGIS `ST_Point(lng lat)` WKT `POINT({lng} {lat})`
- Ignore leftover `MINIO_*` in `.env`; do not implement MinIO

### Phase 6 — Background analysis (threads)
- `app/workers/executor.py` ThreadPoolExecutor, `task_store` in memory
- `POST /api/inspections/<id>/analyze`, `GET /api/tasks/<task_id>`, `GET /api/tasks/<task_id>/result`
- Auto-enqueue after upload
- No Celery/Redis; tasks die on process restart (documented)

### Phase 7 — YOLO detection
- Weights: `artifacts/ai_models/best.pt` (YOLOv8). Classes: longitudinal crack, transverse crack, alligator crack, pothole
- `YOLO_MODEL_PATH`, `YOLO_CONFIDENCE`, `YOLO_IOU`, `YOLO_IMGSZ`, `YOLO_DEVICE=cpu`, `YOLO_FORCE_MOCK`
- Annotated file `*_annotated.jpg` next to original; never overwrite upload
- Persist detections to memory store; DB insert when `detections` is reflected

### Phase 8 — Optional segmentation
- Default **off**: `SEGMENTATION_ENABLED=false`. Mock: `SEGMENTATION_FORCE_MOCK=true`
- **`best.pt` is detection-only** — reject as `SEGMENTATION_MODEL_PATH`
- Pixel % only, not m². Failures must not drop YOLO detections

### Phase 9 — Optional relative depth
- Default **off**. No MiDaS/`torch.hub` download. Relative only, never metric mm/volume

### Phase 10 — Severity engine
- `app/ai/severity_rules.py`: MINOR / MODERATE / SEVERE, priority 1–3
- Review UI `/detections`, `GET /api/detections`

### Phase 11 — Live camera + GIS
- `getUserMedia` on new inspection; capture still → GPS → upload/analyze
- Offline markers via Haversine; online radius via `ST_DWithin`

### Phase 12 — Before/after SSIM
- ORB + RANSAC + SSIM (OpenCV). Thresholds: `VERIFICATION_SSIM_VERIFIED_MAX`, `VERIFICATION_SSIM_PARTIAL_MAX`
- `POST /api/verification/compare`, UI `/repairs`

### Phase 13 — Reports
- PDF/CSV/Excel/JSON under `app/instance/generated_reports/`
- `/reports`, `GET /api/reports/<inspection_id>/pdf|csv|excel|json`

### Phase 14 — Ops
- CSRF, Flask-Limiter `memory://`, security headers, rotating `app/instance/logs/app.log`, `/health` with `rds_contacted`
- JSON errors for `/api/*`; HTML 429

## Test commands (from `municipal_ai_system`)

```bat
venv\Scripts\activate
set YOLO_FORCE_MOCK=true
python -m pytest tests -q
```

Online smoke (home network / hotspot, port 5432 open):

```bat
venv\Scripts\python.exe run.py
```

Then: login → `/dashboard` → `/inspections/new` upload → `/map` markers → `/detections` → `/repairs` → `/reports` → `/health`.

## Error handling you must keep

- GPS missing → manual lat/lng; log warning; do not 500
- ImageKit fail → retry then local fallback; flash “Upload failed, please try again”
- Invalid lat/lng → validate -90..90 / -180..180
- Never return SQL stack traces to the browser

## Done when

- Login works with RDS users (banner gone, `OFFLINE_MODE=false`, real `DATABASE_URL`)
- Upload → ImageKit or local file → GPS on inspection → map GeoJSON
- pytest offline suite passes
- No duplicate template/route names
- README env vars match working `.env` keys
