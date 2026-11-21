# Operations guide

## Local setup (Windows)

All commands run from the project root (`Road Inspection System/`).

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      &REM then set DATABASE_URL, SECRET_KEY, ImageKit keys
start.bat                   &REM or: python run.py
```

Open `http://127.0.0.1:5000`. Linux/macOS: `./start.sh`. Production WSGI entry:
`wsgi:app` (e.g. `gunicorn -w 2 -b 127.0.0.1:5000 wsgi:app` behind Nginx).

All settings are documented in `.env.example`.

## HTTPS (needed for live camera + GPS)

Browsers only allow camera and geolocation on `https://` or `http://localhost`.
In production terminate TLS in front of Flask (Cloudflare, or Nginx + Certbot
proxying to `127.0.0.1:5000`), then set `SESSION_COOKIE_SECURE=true`. If the
camera stays black or location never prompts, check the address is HTTPS and the
site's Camera/Location permissions (iOS Safari asks every session).

## Offline mode

`OFFLINE_MODE=true` runs without the database (in-memory store, local uploads) —
for demos and tests only; nothing is shared or persisted.

## Administration

| Task | How |
|---|---|
| Create role accounts | `python scripts/admin/seed_auth.py` (admin from `SEED_ADMIN_*`), users page `/users` |
| Ward boundaries | `/wards` → Edit → draw or load GeoJSON |
| Severity thresholds | `system_settings.severity_thresholds` (pothole diameter/depth bands) |
| Repair unit rates | insert `system_settings` row `repair_cost_rates` (JSON, keys in `app/ai/cost_engine.py`) |
| SSIM thresholds | `system_settings.ssim_repair_verified_threshold` / `ssim_manual_review_threshold` |
| Deploy a new model | copy `best.pt` + `best.model.json` into `artifacts/ai_models/`, keep `YOLO_IMGSZ=auto`, restart |
| Inspect the DB schema | `python scripts/admin/inspect_schema.py` |

Example — set your own repair rates (values are illustrative):

```sql
INSERT INTO system_settings (setting_key, setting_value, setting_type, category, description, is_editable)
VALUES ('repair_cost_rates',
        '{"source": "Ward SOR 2026-27", "rates": {"pothole_patch_per_m3": 11500}}',
        'json', 'cost', 'Repair unit rates (INR)', true);
```

> **Security:** `scripts/admin/seed_demo_users.py` creates six demo accounts with simple,
> publicly visible passwords. Never run it against the production database; if it
> has been run there, change those passwords or deactivate the accounts.

## Tests

```bat
venv\Scripts\python -m pytest              &REM everything (≈4 min, offline, mock model)
venv\Scripts\python -m pytest tests\unit   &REM fast logic tests
```

`tests/unit` — pure logic; `tests/integration` — Flask test client on the offline
store; `tests/e2e` — real YOLO weights on CPU.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Login shows "Database is not configured" | `DATABASE_URL` missing/placeholder or `OFFLINE_MODE=true` in `.env` |
| `tenant/user ... not found` from Supabase | project paused (free tier) or wrong pooler host — resume it in the Supabase dashboard |
| Every photo gets one centred "pothole" at 0.91 | weights failed to load and the mock detector is active — check the log for `MockYOLOModel` |
| Analysis fails "could not be fetched from ImageKit" | local copy gone and `IMAGEKIT_URL_ENDPOINT` wrong / offline |
| Inspections have no ward | the ward has no boundary polygon yet (`/wards`) |
| Location is the office, not the road | browser location on a PC; pin the map or upload the original photo (WhatsApp strips GPS) |

Logs: `instance/logs/app.log`; audit trail: `instance/logs/audit_trail.jsonl`.
