# Phase 8 / 9 Progress

## Completed
- [x] Repository audit (`docs/phase_8_9_audit.md`)
- [x] Configuration (`SEGMENTATION_*`, `DEPTH_*`) — disabled by default
- [x] Phase 8 segmentation service + mock + YOLO-seg adapter (rejects detection-only `best.pt`)
- [x] Mask processing (area %, union, matching, polygons)
- [x] Segmentation overlay visualization
- [x] Phase 9 depth service + mock + local-weight adapter (no torch.hub download)
- [x] Relative depth stats + overlay (metric/volume unavailable without calibration)
- [x] Severity evidence hooks (compatible when Phase 8/9 disabled)
- [x] Worker pipeline integration (failures preserve detections)
- [x] Memory persistence optional fields
- [x] Offline tests for Phase 8, 9, and pipeline
- [x] Docs / README / `.env.example`

## Findings
- `best.pt` is **detection-only** (`task=detect`)
- No real segmentation or depth weights in repo
- Stack: Flask + Ultralytics + torch + OpenCV + Pillow

## Tests (latest)
```
Phase 8/9 focused: 29 passed
Mock full suite (YOLO_FORCE_MOCK=true): 67 passed, 6 skipped
Real YOLO CPU suite (YOLO_FORCE_MOCK=false): 67 passed, 6 skipped
No real segmentation/depth weight smoke (weights not present)
```

## Remaining limitations
- Real seg/depth require local weights (`SEGMENTATION_MODEL_PATH` / `DEPTH_MODEL_PATH`)
- No metric depth, volume, or physical area without calibration
- Overlay/depth maps stored as local files; raw arrays not written to DB
- RDS/Aurora was not contacted

## Next optional work
- Drop in a real YOLO-seg checkpoint when available
- Drop in a local MiDaS/DPT weights file when available
- Detection review UI
