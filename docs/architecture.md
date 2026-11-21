# Architecture

## System overview

```text
 Field worker phone / office PC (browser)
        │  HTTPS, Jinja2 pages + Alpine.js, Leaflet maps
        ▼
 Flask 3 service  (app/, run from the project root)
   routes/ ─► services/ ─► ai/            YOLO detection, severity, cost, SSIM verifier
      │          │            └─ workers/  in-process thread pool (analysis jobs)
      │          ├─► Supabase Postgres + PostGIS   (system of record)
      │          ├─► ImageKit CDN                  (photo / video storage)
      │          └─► OpenStreetMap Nominatim       (reverse geocoding)
      └─ instance/   logs, temp uploads, generated reports, media cache (local disk)

 scripts/training/  (separate CUDA machine)  →  promotes artifacts/ai_models/best.pt + best.model.json
```

| Layer | Technology |
|---|---|
| Web | Flask 3, Jinja2, Bootstrap 5, Alpine.js, Chart.js, Leaflet 1.9 |
| Auth | Flask-Login sessions, bcrypt, Flask-WTF CSRF, Flask-Limiter |
| Data | Supabase PostgreSQL + PostGIS, SQLAlchemy 2 (6 mapped + 14 reflected tables), GeoAlchemy2 |
| Media | ImageKit (originals), local `instance/` copies for OpenCV / YOLO |
| AI | Ultralytics YOLO (detect), OpenCV (EXIF, ORB alignment, SSIM, ECC), optional YOLO-seg / depth |
| Reports | ReportLab (PDF), openpyxl (Excel), CSV, JSON |
| Background work | `concurrent.futures` thread pool — no Celery/Redis |

## Code layout (`app/`)

| Package | Responsibility |
|---|---|
| `routes/` | Blueprints: `dashboard`, `inspections`, `protected` (admin pages), `api`, `field_worker`, `contractor`, `training` |
| `auth/` | Login, identity (users + super admins), roles, audit of sign-ins |
| `services/` | Business logic: inspections, GPS, wards, detections, reviews, repairs, verification, reports, access control |
| `ai/` | Model loading + inference, ground geometry, severity engine, cost engine, before/after verifier, optional segmentation/depth |
| `workers/` | Analysis job runner (`inspection_worker`) and task store |
| `models/` | SQLAlchemy models; `reflection.py` reflects the remaining Supabase tables |
| `utils/` | Paths, EXIF orientation, media cache, validation helpers |
| `templates/`, `static/` | Server-rendered UI |

## Persistence model

Supabase is the system of record. Some workflow objects also keep an in-process
working set for speed:

| Object | Stored in | Notes |
|---|---|---|
| Inspections, media | `inspections`, `inspection_media` | ORM models |
| Detections | `detections` (+ memory cache with the **same IDs**) | size, assumed depth, volume, cost, repair type stored |
| Detection reviews | `detections` status columns + `detection_reviews` audit rows | |
| Repair jobs | `tasks` (`task_type='repair'`) | app-only fields in `tasks.repair_materials.app_workflow`; working set reloaded every 5 s |
| Before/after result | summary on the repair job (`app_workflow.verification`) | full comparison detail is in-process only |
| Analysis job progress | in-process task store | inspection status itself is persisted |
| Ward boundaries | `wards.ward_boundary` (PostGIS polygon) | drawn / imported on the ward edit page |
| Settings | `system_settings` | severity thresholds, priority map, SSIM thresholds, repair cost rates |

`OFFLINE_MODE=true` (and the test suite) run entirely on the in-memory/offline
store, with no database.

## Access control

`app/services/access_service.py` is the single rule for “who may see this inspection”
(and therefore its media, detections, reports, analysis jobs):

* super admin — everything
* field worker — inspections they uploaded
* contractor — inspections with a repair job assigned to them
* municipal admin, field officer, viewer — their own municipality

Role gates per route are applied with `@roles_required` (`app/decorators.py`).

## Runtime data

Everything the running service writes goes under `INSTANCE_DIR`
(default `instance/` in the project root): `logs/` (app log + audit trail),
`temp/inspections/` (local upload copies, annotated images), `generated_reports/`,
`media_cache/` (originals fetched back from ImageKit). Nothing is written inside
the `app` package.
