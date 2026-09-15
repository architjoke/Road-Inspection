# Master prompt — RTX 4060 train v2 (NO AMP, road-heavy mix)

**How to use:** Copy everything under `COPY FROM HERE` into a **new Cursor chat** on the 4060 laptop (`c:\Road Inspection\` or wherever `data/` + scripts already live).

---

COPY FROM HERE
---

# Role

You are training a **YOLOv8 road-damage detector** on Windows with an **NVIDIA RTX 4060**.  
Workspace already exists (or create it) with `data/road`, `data/Crack dataset`, scripts, and venv.

## Critical lessons from failed runs (MUST follow)

1. **AMP breaks this GPU setup** — train loss hits NaN, val metrics become **all zeros** from ~epoch 6–11.  
   → Always use **`amp=False` / `--no-amp`**. Never enable AMP on this machine.
2. **Too much crack data dilutes road/potholes** — full crack (~8k) + road (~5.3k) made crack ~60% of images and class 0 ~90% of boxes.  
   → Crack train images = **40% of road train count** (~2147), seed **42**.
3. Early “best” checkpoints from broken AMP runs are **weak** — do not resume them.  
   → Start **fresh from `yolov8s.pt`**.
4. Old road-only export had mAP50 ~**0.26** on road val. Do **not** replace `EXPORT/best.pt` if the new model is worse on road.

## Flask class IDs (do not change)

| id | name |
|----|------|
| 0 | D00_longitudinal_crack |
| 1 | D10_transverse_crack |
| 2 | D20_alligator_crack |
| 3 | D40_pothole |

Crack mask boxes → class **0**. Skip `noncrack*`. Keep **all** rare class-1 road samples.

---

## Mission (order mandatory)

1. **PLAN FIRST** — confirm GPU, Python 3.11/3.12, data counts, kill any running AMP train. Print the locked recipe. **STOP until I say approve/go.**
2. After approval: rebuild combined mix → train **no AMP** → dual validation → export only if gate passes.
3. If road val mAP50 drops **>10% relative** vs current good `EXPORT/best.pt` (~0.26): one retry with **30%** crack fraction, still no AMP.

---

## Phase 0 — Plan (do now, then STOP)

1. Confirm:
   - `torch.cuda.is_available()` True, device name contains 4060
   - venv Python is **3.11 or 3.12** (not 3.14)
2. Kill any active training process that uses AMP / shows NaN or zero val metrics.
3. Audit counts:
   - `data/road` train/val/test
   - `data/crack` if present
   - current `data/combined` if present
4. State locked recipe below and ETA (~8–14 h without AMP).
5. **STOP for approval.**

---

## Locked recipe (after approve)

### Data mix
- Rebuild `data/combined/`:
  - **100%** of `data/road` train/val/test
  - Crack train sample = **0.40 × road_train_count** (≈2147), `--seed 42`
  - Crack val cap ≈ **500** (so combined val is not crack-dominated)
- Do not delete originals under `data/road` or `data/Crack dataset`.
- Fix `data/road/data.yaml` `path:` to this machine before road-only val.

Example:

```bat
.\.venv\Scripts\python.exe scripts\build_combined_dataset.py --data-root data --crack-train-frac 0.40 --seed 42 --crack-val-cap 500
```

(Adjust script flags to whatever exists; create/update script if flags missing.)

### Train
- Model: **fresh `yolov8s.pt`** (no resume from broken runs)
- imgsz: **640**
- epochs: **100**
- patience: **30**
- batch: **16** (drop to **12** if OOM)
- **amp: False** (`--no-amp`)
- device: **0**
- workers: **2** (Windows-stable)
- close_mosaic: **12**
- run name: `road_crack_yolov8s_v2_noamp`

Example:

```bat
.\.venv\Scripts\python.exe scripts\train_yolo.py --data data\combined\data.yaml --model yolov8s.pt --epochs 100 --patience 30 --batch 16 --imgsz 640 --device 0 --workers 2 --no-amp --name road_crack_yolov8s_v2_noamp
```

If `train_yolo.py` has no `--no-amp`, set `amp=False` inside the Ultralytics `model.train(...)` call. **Verify the train log shows AMP disabled.**

### Live health checks during train
- If **any epoch** shows val mAP all zeros OR train loss NaN → **stop immediately**, do not wait for patience. Fix (confirm amp off, lower batch), restart.
- Metrics should stay non-zero and generally trend up after the first few epochs.

### After train — mandatory A/B gate
Validate **both** models on the same sets:

1. New run `best.pt`
2. Current `EXPORT/best.pt` (previous export)

On:
- `data/road/data.yaml` (val)
- `data/combined/data.yaml` (val)

Report table: Precision, Recall, mAP50, mAP50-95, **per-class AP** (especially class 3 pothole and class 0 crack).

### Replace EXPORT only if
- Road val mAP50 drop ≤ **10% relative** vs current EXPORT (baseline ~0.26), AND
- Road mAP50-95 is not clearly worse, AND
- Class 3 (pothole) does not collapse

Else: rebuild at `--crack-train-frac 0.30`, retrain once (still **no AMP**), repeat gate.

### EXPORT package
```text
EXPORT/
├── best.pt
├── best_road_crack_yolov8s_v2_noamp.pt
├── metrics_summary.txt
└── INTEGRATE.txt
```

`INTEGRATE.txt`:
```text
On other PC:
1. Backup municipal_ai_system/app/ai_models/best.pt
2. Copy EXPORT/best.pt → municipal_ai_system/app/ai_models/best.pt
3. YOLO_FORCE_MOCK=false, YOLO_DEVICE=cpu
4. Restart Flask and test uploads
```

Note: PyTorch `.pt` may look like a zip internally — deliver the file as **`best.pt`**, not only `best.pt.zip`, unless you also document “copy zip to best.pt”.

---

## Success definition
Stable multi-epoch training with **non-zero** val metrics; road performance close to or better than previous good export; usable pothole (3) + crack (0) detections without the dilution/AMP failure mode.

## First reply to me
1. GPU + Python version  
2. Confirm AMP run killed (if any)  
3. Current data counts + proposed 40% crack numbers  
4. Exact train command with **no-amp**  
5. **STOP until I approve**
