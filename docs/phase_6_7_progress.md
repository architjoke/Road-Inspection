# Phase 6 / 7 Progress

## Completed
- [x] Repository audit
- [x] Phase 6/7 implementation
- [x] Local integration validation & hardening
  - TaskStore: active-task reuse, retention prune, thread-safe updates
  - Executor: locked init, graceful shutdown, test reset helper
  - Model config: env overrides, relative path resolution
  - TESTING app uses sqlite memory (no RDS reflection)
  - API lifecycle tests (404/202/200/FAILED/duplicate/concurrent)
  - Real-model CPU smoke tests against `app/ai_models/best.pt`
- [x] Local end-to-end acceptance (no RDS/Aurora)
  - `OFFLINE_MODE=true` / `TESTING` → in-memory `offline_store` for inspections/media
  - Upload → Phase 5 validation → local storage → auto-analyze → poll → result
  - Detection JSON + annotated image (`*_annotated.jpg`)
  - `YOLO_FORCE_MOCK=true` and `false` verified
  - Duplicate analyze reuses active task
  - Invalid image / missing inspection ID → controlled 400/404
  - Fixture: `app/tests/fixtures/road_sample.jpg`
  - Tests: `app/tests/test_local_e2e_acceptance.py`

## Startup (local offline)

```bash
cd municipal_ai_system
set OFFLINE_MODE=true
set YOLO_FORCE_MOCK=false
python run.py
```

Health: `GET /health` → `database_configured: false`, `offline_mode: true`

## Tests

```
YOLO_FORCE_MOCK=true  OFFLINE_MODE=true  → 38 passed, 6 skipped
YOLO_FORCE_MOCK=false YOLO_DEVICE=cpu OFFLINE_MODE=true → 38 passed, 6 skipped
```

Commands:

```bash
cd municipal_ai_system
set YOLO_FORCE_MOCK=true
set OFFLINE_MODE=true
python -m pytest app/tests -q

set YOLO_FORCE_MOCK=false
set YOLO_DEVICE=cpu
set OFFLINE_MODE=true
python -m pytest app/tests -q
```

E2E-only:

```bash
python -m pytest app/tests/test_local_e2e_acceptance.py -q
```

## Database integration status
- Offline default: **in-memory** detection repository + **offline_store** for inspections/media
- Optional: reflected `detections` table insert when DB models are loaded
- No new Alembic migration added (existing DBeaver schema + reflection)
- **RDS/Aurora was not contacted**

## Remaining limitations
- Analysis tasks are process-local (lost on restart)
- Offline store is process-local (not a substitute for PostGIS in production)
- Detection DB writes only when reflection succeeds against a live DB
- Detection review UI still placeholder
- Nominatim / ImageKit skipped when `OFFLINE_MODE` or `TESTING`
- Phase 8/9 optional; no real seg/depth weights bundled (`best.pt` is detection-only)

## Next optional work (not blocking)
- Persist tasks to reflected `tasks` table when online
- Detection review UI
- Provide local YOLO-seg / depth weights for real Phase 8/9 inference
