# AI pipeline: detection, severity, cost, verification

## 1. Detection

* Model: Ultralytics YOLO, 4 classes — `D00_longitudinal_crack`, `D10_transverse_crack`,
  `D20_alligator_crack`, `D40_pothole` (`artifacts/ai_models/best.pt`).
* Photos are made **upright** first (EXIF orientation), so boxes, masks and the
  annotated image share one frame (`app/utils/image_orientation.py`).
* `best.model.json` (written when a model is promoted) supplies the training image
  size (`YOLO_IMGSZ=auto`) and **per-class confidence thresholds** tuned on India val.
* Video: 1 frame/s, first 8 s; dashcam camera pose assumed for sizes.

**Current model status (2026-11-21):** the production `best.pt` was trained on a
mislabelled copy of RDD India in which "pothole" meant faded lane paint; it finds
about 41 % of damage (mAP50 0.43). Corrected data (`data/road_v2`) and the
retraining recipe are ready — see [ml/retraining.md](ml/retraining.md).

## 2. Physical size (`app/ai/ground_geometry.py`)

Each box is projected onto the road plane with a pinhole model: focal length from
EXIF `FocalLengthIn35mmFilm` when present (else 66° FOV), assumed camera height
1.4 m and 30° downward tilt (video: 1.3 m, 12°). Output: width, length, distance.
Boxes near the horizon or > 20 m away are marked unreliable. Typical accuracy is
±30–40 % when the pose assumption holds.

## 3. Severity (`app/ai/severity_engine.py`, rule version `phase10-v2-geometry`)

| Damage | Measure | Moderate ≥ | Severe ≥ |
|---|---|---|---|
| Pothole | average diameter | 250 mm | 500 mm (from `system_settings.severity_thresholds`) |
| Alligator | area | 0.5 m² | 3 m² |
| Longitudinal / transverse | crack length | 2 m | 5 m |

* Segmentation masks (when enabled) refine the area; relative depth can raise a
  pothole/alligator one level.
* **Confidence never sets severity** — it only triggers manual review
  (< 0.35 low confidence; severe with < 0.45).
* Unreliable size → image-fraction fallback + manual review.
* Pothole depth is *assumed* per band (20 / 35 / 75 mm) and labelled as such.

## 4. Repair cost (`app/ai/cost_engine.py`)

Quantity × unit rate (INR):

| Damage | Quantity | Default rate (placeholder) |
|---|---|---|
| Pothole | patch volume m³ = (L + 0.3 m)(W + 0.3 m) × depth | ₹12,000 / m³ |
| Cracks | sealing length m | ₹120–300 / m by severity |
| Alligator | treated area m² incl. margin | ₹180 / 650 / 1,800 per m² |

Minimum ₹500 per item; inspection total adds 10 % contingency and one ₹2,000
mobilisation. **Replace the placeholder rates with your Schedule of Rates** by
inserting a JSON row into `system_settings` (`setting_key = 'repair_cost_rates'`,
any subset of the keys in `DEFAULT_COST_RATES`). Cost and size are stored on each
detection and the total on the inspection.

## 5. Before/after verification (`app/ai/before_after_verifier.py`, `phase12-v3-roi`)

1. Downscale to 1,280 px, normalise lighting (CLAHE).
2. Align the after photo (ORB + RANSAC homography); reject clearly different roads.
3. Score SSIM only where both photos overlap (no black warp borders).
4. With the detection box: refine alignment locally (ECC on the surrounding road),
   then compare **damage-box SSIM vs surrounding-road SSIM**. The damage area must
   change ≥ 0.15 more than its surroundings to count as repaired.
5. Extra checks: after-photo GPS within 30 m of the inspection, AI re-check of the
   repaired box.
6. Auto-verify (which can trigger payment) only when nothing is flagged; otherwise
   the reasons are shown and a person decides. Thresholds from `system_settings`.

Measured on simulated PC uploads of real Indian road photos: 12/12 real repairs
verified, 0/12 unrepaired and 0/12 different-road photos accepted (the previous
verifier auto-verified 12/12 unrepaired photos taken from closer).

## 6. Optional models

* **Crack outlines** (YOLO-seg on crack-box crops) — dataset and training profile
  ready (`scripts/training/build_crack_seg.py`, profile `crack_seg`); app integration after
  training.
* **Relative depth** — `DEPTH_ENABLED`; never claims metric depth.

## 7. Model files (`artifacts/ai_models/`)

| File | Role |
|---|---|
| `best.pt` | active detection weights (YOLOv8s, 4 classes, detect only) — trained on the mislabelled pothole data, to be replaced |
| `best.model.json` | model card written by `scripts/training/evaluate.py --promote`: training image size, per-class thresholds, metrics |
| `best_backup_<timestamp>.pt` | previous weights, kept automatically on promotion (git-ignored) |

* Path: `YOLO_MODEL_PATH` (default `artifacts/ai_models/best.pt`). If the weights
  fail to load the app falls back to a mock detector — check the log.
* Optional segmentation (`SEGMENTATION_MODEL_PATH`, a YOLO-seg checkpoint — never
  `best.pt`) and relative depth (`DEPTH_MODEL_PATH`) weights also live here; both
  are off by default and nothing is downloaded automatically.
