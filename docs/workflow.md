# Workflow, roles and routes

## Roles

| Role | Can do |
|---|---|
| Super admin | Everything, all municipalities; municipalities, settings, model test page |
| Municipal admin | Users and wards of their municipality, review detections, create/assign repairs, verify, reports |
| Field officer | Review detections, create repairs, run before/after verification, reports |
| Field worker | Capture / upload inspections, see their own inspections |
| Contractor | See assigned repair jobs, upload after-repair evidence |
| Viewer | Read-only dashboards, map, inspections and reports of their municipality |

## End-to-end flow

1. **Capture** — field worker uses live camera (`/field_worker/dashboard`) or upload
   (`/inspections/new`). Image must be ≥ 480 px on the short side (≥ 720 recommended).
   Location, in order of trust: map pin → GPS stored in the photo/video → browser
   location (flagged above ±100 m, refused above ±5 km). Ward assigned from the
   ward polygons; street address from Nominatim.
2. **Analysis** (background job, starts automatically) — photo made upright (EXIF),
   YOLO detection with per-class thresholds, size estimate on the road plane,
   severity (size-based), repair type and cost; annotated image saved; detections
   stored in Supabase; an unassigned repair job is created per detection.
   Re-running analysis replaces earlier *unreviewed* results.
3. **Review** — admin / field officer approves, rejects (false positive) or sends a
   detection for field check (`/detections`). Stored on the detection + audit row.
4. **Repair** — admin assigns the job to a contractor (`/repairs`); contractor
   uploads the after photo (`/contractor/jobs/<id>`).
5. **Verify** — field officer runs before/after verification (`/repairs/verify`):
   alignment, damage-box vs surrounding-road SSIM, GPS check, AI re-check. Clear
   results auto-verify the job; anything flagged goes to a person.
6. **Report** — PDF / Excel / CSV / JSON per inspection (`/api/reports/<id>/<fmt>`)
   with size, cost breakdown, verification and recommendations.

## Statuses

| Object | Values |
|---|---|
| Inspection | `pending_analysis` → `processing` → `pending_review` |
| Detection | `PENDING_REVIEW` → `APPROVED` / `REJECTED` / `NEEDS_FIELD_CHECK` |
| Repair job (app) | `open` → `assigned` → `in_progress` → `evidence_uploaded` → `verified` → `closed` / `paid`; `rejected` |
| Repair job (`tasks.status`) | `pending`, `assigned`, `in_progress`, `completed`, `reopened` (mapped from the app status) |

## Main pages

| Path | Who |
|---|---|
| `/dashboard` | all (role-specific dashboard) |
| `/inspections/`, `/inspections/<id>`, `/inspections/new` | staff / own inspections for field workers |
| `/map` | all roles (contractors see their jobs) |
| `/detections`, `/detections/<id>` | super admin, municipal admin, field officer |
| `/repairs`, `/repairs/new`, `/repairs/<id>`, `/repairs/verify` | staff |
| `/contractor/jobs` | contractor |
| `/wards`, `/wards/new`, `/wards/<id>/edit` | admins (boundary editor) |
| `/users`, `/municipalities`, `/settings`, `/training/` | admins / super admin |
| `/reports` | staff, viewer |

## Main APIs (`/api`)

| Endpoint | Purpose |
|---|---|
| `POST /inspections/upload`, `POST /field_worker/api/capture` | create inspection with media |
| `POST /inspections/<id>/analyze`, `GET /tasks/<id>`, `GET /tasks/<id>/result` | analysis jobs |
| `GET /detections`, `GET /detections/<id>`, `POST /detections/<id>/review` | detections + review |
| `POST /verification/compare`, `GET /verification`, `GET /verification/<id>` | before/after verification |
| `POST /repairs/<id>/mark-verified` | manual verification |
| `GET /inspections/geojson`, `GET /map/detections`, `GET /inspections/radius`, `GET /wards/boundaries` | map data |
| `GET /reports/<id>/{json,csv,excel,pdf,summary}`, `GET|POST /reports/export` | reports |
| `GET /analytics/summary`, `GET /geocode/reverse` | dashboards, address lookup |
