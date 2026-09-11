# Phase 8 / 9 Repository Audit

**Date:** 2026-09-11  
**Scope:** `municipal_ai_system`  
**RDS/Aurora connectivity was not checked and is not required.**

## Actual stack found

| Layer | Technology |
|-------|------------|
| Web | Flask 3 |
| ORM / GIS | SQLAlchemy 2 + GeoAlchemy2 / PostGIS (online only) |
| Frontend | Jinja2 + Bootstrap + Leaflet |
| Storage | ImageKit + local temp fallback |
| Jobs | ThreadPoolExecutor + in-memory TaskStore |
| Detection | Ultralytics YOLOv8 + torch + OpenCV + Pillow |
| Offline | `OFFLINE_MODE` / `TESTING` + `offline_store` |

## Model files

| Path | Task | Notes |
|------|------|-------|
| `app/ai_models/best.pt` | **detect only** | Ultralytics `DetectionModel`, head=`Detect`, no seg layers |
| Other `.pt/.pth/.onnx` | **None** | No segmentation or depth weights checked in |

**`best.pt` is detection-only and must not be used as a mask model.**

## Existing Phase 8 / 9 code

| File | Status |
|------|--------|
| `app/ai/segmentation.py` | Empty stub |
| `app/ai/depth_estimation.py` | Empty stub |
| SegFormer / MiDaS / SAM in app | Not present |
| Mask / depth persistence fields | Not present |
| Worker hooks for seg/depth | Not present |

## Dependency status

- Present: `ultralytics`, `torch`, `opencv-python-headless`, `Pillow`
- Not present: SegFormer/`transformers`, MiDaS packages, local depth weights
- Decision: implement mock adapters + optional real loaders that require an explicit local path (no auto-download)

## Reusable components

- `app/ai/model_loader.py` — singleton / env / mock pattern
- `app/ai/yolo_inference.py` — detection (unchanged)
- `app/ai/image_annotator.py` — extend for overlays
- `app/ai/detection_postprocessor.py` / `severity_engine.py` — severity hooks
- `app/workers/inspection_worker.py` — pipeline extension after detect
- `app/services/detection_service.py` — memory persistence
- `app/config.py` — env config pattern
- `app/tests/conftest.py` — offline test resets

## Missing features (to implement)

1. Segmentation service + mock + optional YOLO-seg adapter
2. Mask processing (area %, union, detection matching)
3. Mask visualization
4. Depth service + mock + optional local-weight adapter
5. Relative depth stats + visualization
6. Worker integration (optional, non-breaking)
7. Config / `.env.example` / README
8. Offline tests

## Files to create / modify

**Create:** mask/depth utils if needed; tests; `docs/phase_8_9_progress.md`  
**Fill:** `segmentation.py`, `depth_estimation.py`  
**Modify:** `config.py`, worker, annotator, severity, detection_service, README, `.env.example`

## Test command

```bash
cd municipal_ai_system
set OFFLINE_MODE=true
set YOLO_FORCE_MOCK=true
python -m pytest app/tests -q
```

## Limitations

- No real segmentation or depth weights in repo → mock-first; real adapters require local files
- Relative depth only; no metric depth/volume without calibration
- Pixel area % only; not square meters
