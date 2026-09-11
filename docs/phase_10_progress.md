# Phase 10 Progress — Damage Severity Engine

## Completed
- [x] Audit (`docs/phase_10_audit.md`)
- [x] Configurable rules (`app/ai/severity_rules.py`)
- [x] Hybrid severity engine (`app/ai/severity_engine.py`)
- [x] Detection postprocessor wired to engine
- [x] Worker integration + inspection-level `severity` summary
- [x] Memory persistence of severity fields
- [x] Config / `.env.example` / README
- [x] Offline tests (`test_severity_engine.py`)
- [x] Detection review API + minimal UI (this checkpoint)
  - `GET /api/detections`, `GET /api/detections/<id>`
  - `/detections` review page (severity, priority, action, area, relative depth, manual-review)
  - `detection_review_service.py`, `test_detection_review.py`

## Rules (defaults)
- Levels: MINOR / MODERATE / SEVERE
- Priority: 1 / 2 / 3
- Detection baseline + bbox fallback
- Segmentation area when available
- Relative depth supporting only (never metric)
- Low confidence / conflicts → `manual_review_required`
- Actions: emergency inspection / priority repair / scheduled monitoring
- Class-specific repair type strings (not an official municipal standard)

## Tests
```
Phase 10 focused (severity + review): 19 passed
Mock suite (YOLO_FORCE_MOCK=true): 86 passed, 6 skipped
Real YOLO CPU suite (YOLO_FORCE_MOCK=false): 86 passed, 6 skipped
```

## Changed files (this unit)
- `app/services/detection_review_service.py` (new)
- `app/routes/api.py`
- `app/routes/protected.py`
- `app/routes/detections.py`
- `app/templates/detections/review.html`
- `app/static/css/detections.css`
- `app/tests/test_detection_review.py`
- `README.md`
- `docs/phase_10_progress.md`

## Remaining limitations
- Thresholds are file/env based (not DB-backed yet)
- Review UI reads in-memory detections only (process-local)
- No official municipal code table claimed
- Relative depth is not physical depth
- Pixel / normalized area only (not m²)

## RDS/Aurora
Not contacted.

## Next optional work
- DB-backed severity thresholds when online reflection is available
- Wire review page to reflected detections table when RDS is reachable
