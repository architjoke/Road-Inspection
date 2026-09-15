# AI model assets

## Detection (Phase 7) — present

Trained Ultralytics YOLOv8 **detection** weights:

| File | Role |
|------|------|
| `best.pt` | **Active** v2 weights (road+crack, no-AMP, yolov8s, best ~epoch 73) |
| `best_combined_weak_20260914.pt` | Backup of earlier combined export (weaker on road val) |
| `best_road_only_20260911.pt` | Backup of earlier India-road-only weights |

- Task: **detect only** (not segmentation)
- Config: `YOLO_MODEL_PATH` (default: this directory’s `best.pt`)
- Classes (RDD2022):
  - 0 `D00_longitudinal_crack`
  - 1 `D10_transverse_crack`
  - 2 `D20_alligator_crack`
  - 3 `D40_pothole`
- `YOLO_FORCE_MOCK=true` for offline tests without loading weights
- Do not keep duplicate `best.pt.zip` or extracted zip folders here — PyTorch `.pt` is already a zip archive internally.

## Segmentation (Phase 8) — optional, not bundled

Place a **YOLO-seg** (or other supported) checkpoint here and set:

```
SEGMENTATION_ENABLED=true
SEGMENTATION_FORCE_MOCK=false
SEGMENTATION_MODEL_PATH=app/ai_models/<your_seg_weights>.pt
```

Do **not** point `SEGMENTATION_MODEL_PATH` at `best.pt` — it is detection-only.

Without weights, use mock mode:

```
SEGMENTATION_ENABLED=true
SEGMENTATION_FORCE_MOCK=true
```

Damaged area is reported as **pixel percentage**, not square meters.

## Depth (Phase 9) — optional, not bundled

Place local monocular depth weights and set:

```
DEPTH_ENABLED=true
DEPTH_FORCE_MOCK=false
DEPTH_MODEL_PATH=app/ai_models/<your_depth_weights>.pt
```

No MiDaS/`torch.hub` auto-download. Output is **relative** depth only.
Metric depth / volume require camera calibration (not available in this project).
