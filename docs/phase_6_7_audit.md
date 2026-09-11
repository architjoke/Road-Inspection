# Phase 6 / 7 Repository Audit

**Date:** 2026-09-11  
**RDS/Aurora connectivity was not checked and is not required.**

## Actual stack found

| Area | Technology |
|------|------------|
| Backend | Flask 3.0.3 + Flask-SQLAlchemy 3 + Flask-Login / WTF / Limiter |
| DB access | SQLAlchemy 2 + GeoAlchemy2; explicit ORM for 6 tables; reflection for rest |
| Storage | ImageKit (`imagekit_service`) + local `TEMP_UPLOAD_ROOT` fallback |
| Frontend | Jinja2 + Bootstrap 5 + Alpine.js + Leaflet + Chart.js |
| Background jobs | **None implemented** (empty stubs: `workers/*`, `task_service.py`) |
| AI | Empty stubs under `app/ai/*`; Ultralytics **8.1.47** present in project `venv` |
| Entry | `run.py` → `create_app()` |

## Phase 5 completed (reuse)

- `imagekit_service`, `gps_service`, `geocoding_service`, `video_service`, `validation_service`, `media_service`, `inspection_service`
- Upload routes `/inspections/new`, `/api/inspections/upload`
- GeoJSON / radius / wards APIs

## Phase 6 existing

- Empty: `workers/executor.py`, `inspection_worker.py`, `video_worker.py`, `services/task_service.py`, `routes/tasks.py`, `models/task.py`
- Hook stub: `_queue_analysis_stub()` in `inspection_service.py`

## Phase 7 existing

- Empty: `ai/yolo_inference.py`, `model_loader.py`, `image_annotator.py`, `detection_postprocessor.py`, `severity_engine.py`, `detection_service.py`, `models/detection.py`
- Notebooks define RDD2022 classes (training reference)

## Trained weights

| Found | Path | Format | Size |
|-------|------|--------|------|
| Yes | `app/ai_models/best.pt` | Ultralytics YOLO `.pt` | ~22 MB |

**Class names (from notebooks / RDD2022):**
- 0 `D00_longitudinal_crack`
- 1 `D10_transverse_crack`
- 2 `D20_alligator_crack`
- 3 `D40_pothole`

## Missing functionality (to build)

1. Bounded `ThreadPoolExecutor` + in-memory task store (offline-safe)
2. Analysis worker: media → detect → persist → status update
3. YOLO loader (singleton) + mock detector for offline tests
4. Annotation writer (never overwrite originals)
5. Detection persistence (DB when available; in-memory for tests)
6. API: submit analysis, task status/result
7. Wire upload → enqueue analysis
8. Offline pytest suite

## Files to modify

- `app/config.py`, `app/__init__.py`, `app/routes/api.py`, `app/routes/__init__.py`
- `app/services/inspection_service.py`, `app/utils/status_constants.py`
- `requirements.txt`, `.env.example`, `README.md`

## Files to create / fill

- `docs/phase_6_7_audit.md`, `docs/phase_6_7_progress.md`
- Fill empty stubs listed above
- `app/workers/task_store.py` (new)
- `app/tests/conftest.py` + Phase 6/7 tests

## Test command

```bash
cd municipal_ai_system
python -m pytest app/tests -q --tb=short
```

## Blockers

- None for offline implementation. Live DB persistence optional; in-memory fallback required for tests.
- Do not download weights or connect to RDS.
