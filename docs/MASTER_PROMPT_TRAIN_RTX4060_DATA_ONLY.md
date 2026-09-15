# Master prompt — Train road + crack YOLO on RTX 4060 (`data/` folder only)

**How to use:** Copy everything under the line `COPY FROM HERE` into a **new Cursor chat on the 4060 laptop**.  
That laptop should have **only** your `data/` folder (with `road/` and `Crack dataset/`). No Flask app required.

On your main PC after training: copy `EXPORT/best.pt` → `municipal_ai_system/app/ai_models/best.pt`, backup the old weights first, restart Flask.

---

COPY FROM HERE
---

# Role

You are an expert ML engineer helping me train a **YOLOv8 road-damage detector** on a Windows laptop with an **NVIDIA RTX 4060 (8 GB VRAM)**.

I have **only** a folder named `data/` on this machine. There is **no** Flask repo, **no** notebooks, **no** existing scripts. You must create everything needed: venv, scripts, conversion, combined dataset, training, and an export package I can copy to another PC.

## Mission (order is mandatory)

1. **PLAN FIRST** — audit data, write a short plan with counts + train recipe + time estimate. **STOP and wait for my approval** before long training.
2. After I approve: install environment, **tune** mask→box conversion, build combined dataset.
3. Smoke train, then full train for **maximum precision and accuracy**.
4. Export `EXPORT/best.pt` + a short handoff note so I can drop it into Flask on my other laptop.

## Hard constraints

- Do **not** delete or overwrite original `data/road` or `data/Crack dataset` contents. Write new outputs under `data/crack/`, `data/combined/`, `runs/`, `EXPORT/`.
- Class IDs must stay Flask-compatible (**nc: 4**):

| id | name |
|----|------|
| 0 | D00_longitudinal_crack |
| 1 | D10_transverse_crack |
| 2 | D20_alligator_crack |
| 3 | D40_pothole |

- Untyped crack-segmentation boxes → class **0** only.
- Skip any file whose name starts with `noncrack` (case-insensitive).
- Prefer **archive** over delete if you create junk.
- Do not commit large images to git (local training only).
- Final production weights must be **4-class**, not a 1-class crack-only model (crack-only is allowed only as a short conversion debug).

## Expected `data/` layout (search if names differ)

```text
data/
├── road/
│   ├── train/images + labels      # India RDD YOLO — already filtered
│   ├── val/images + labels
│   ├── test/images + labels
│   └── data.yaml                  # may have wrong absolute path — fix later
├── Crack dataset/                 # OR "Crack dataset" / similar
│   ├── train/images + train/masks
│   └── test/images + test/masks
└── crack/                         # may be empty — you will fill YOLO boxes here
```

**Expected counts if copy is complete:**

- Road: train ≈ 5368, val ≈ 1178, test ≈ 1166 (images ≈ labels)
- Crack masks: train ≈ 9603 images, test ≈ 1695 (before skipping `noncrack*`)

If counts differ a lot, report and adapt; do not invent missing labels.

---

## Phase 0 — Discover + PLAN (do this first; then STOP)

1. Locate the folder that contains `road/` and the crack mask dataset. Call it `DATA_ROOT`.
2. Create workspace **next to** `DATA_ROOT` (or with `DATA_ROOT` inside it):

```text
RoadTrain4060/
├── data/                 # my copied data (or symlink/junction to it)
├── scripts/
├── requirements.txt
├── EXPORT/
├── runs/                 # ultralytics outputs
└── README_HANDOFF.md
```

3. Print audit:
   - Road train/val/test image & label counts; empty-label rate; box counts per class 0–3
   - Crack train/test image & mask pair counts; how many `noncrack*`
   - Confirm GPU with planned install steps (even if not installed yet)

4. Propose this **locked** train recipe (do not weaken it without my OK):

| Setting | Value |
|---------|--------|
| Combined dataset | `data/combined/` = all road YOLO + converted crack (class 0) |
| Smoke | `yolov8n.pt`, epochs 3, imgsz 640, subset ~800 train images |
| Full model | **`yolov8s.pt`** |
| imgsz | 640 (escalate once to 800 only if smoke shows weak thin cracks) |
| epochs | 80 |
| patience | 20 |
| batch | 16–24 (fit 8 GB; lower if OOM) |
| amp | True |
| device | 0 |
| optimizer | default Ultralytics (AdamW/SGD as YOLO default for version) |
| mosaic | default on; close mosaic in final ~10 epochs if supported |
| Goal metrics | Maximize **precision** and **mAP50-95** on val; also report mAP50 / recall |

5. Time estimate for 4060 (rough): smoke 5–15 min; full **~2–5 hours**.

**STOP here. Wait for me to say “approve” / “go” before installing long deps or training.**

---

## Phase 1 — Environment install (after approval)

From `RoadTrain4060/`:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install ultralytics opencv-python-headless pyyaml Pillow matplotlib
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"
```

Requirements:

- `torch.cuda.is_available()` must be **True**
- Device name should include **4060**
- If CUDA fails: fix driver / wheel before any train (do not fall back to CPU for the full run)

Write `requirements.txt` with pinned versions you actually installed.

---

## Phase 2 — Scripts you must create

Create these under `scripts/` (Python 3.11, pathlib, opencv, ultralytics):

### 2.1 `scripts/audit_datasets.py`

- Args: `--data-root`
- Prints road/crack counts, class histograms, broken pairs
- Exit non-zero if road train has 0 images

### 2.2 `scripts/convert_crack_masks_to_yolo.py`

- Input: `data/Crack dataset/{train,test}/{images,masks}` (find real path)
- Output: `data/crack/{train,val}/{images,labels}` where **test → val**
- Skip `noncrack*`
- Mask → YOLO AABB via contours (same idea as typical `mask_to_yolo_lines`):
  - grayscale, threshold, optional morphology open
  - `min_area` default 40 (tunable CLI flag)
  - normalized `cls xc yc w h`, cls always **0**
- Copy/link images into `data/crack/.../images`
- Write `data/crack/data.yaml` (1-class debug ok)
- Write **at least 12 preview overlays** to `data/crack/_preview/` (image + green boxes)
- Print: images kept, boxes/image stats, empty masks skipped

### 2.3 `scripts/build_combined_dataset.py`

- Build `data/combined/{train,val,test}/{images,labels}`:
  - Copy from `data/road` (all 4 classes) into matching splits
  - Add crack train → combined train; crack val → combined val
  - Prefer **copy** or hardlink; do not destroy sources
- Write `data/combined/data.yaml`:

```yaml
path: <absolute path to data/combined>
train: train/images
val: val/images
test: test/images
nc: 4
names:
  0: D00_longitudinal_crack
  1: D10_transverse_crack
  2: D20_alligator_crack
  3: D40_pothole
```

- Print final image counts and class box histogram (expect class **1** rare — keep all samples anyway)

### 2.4 `scripts/train_yolo.py`

CLI flags:

- `--data` path to yaml (default `data/combined/data.yaml`)
- `--model` default `yolov8s.pt`
- `--epochs`, `--imgsz`, `--batch`, `--patience`, `--device`, `--name`, `--smoke`

Behavior:

- Smoke mode: yolov8n, 3 epochs, optional fraction/subset
- Full mode: yolov8s, 80 epochs, patience 20, amp=True, plots=True, save=True
- Project dir `runs/detect`
- After train: copy `best.pt` → `EXPORT/best.pt`
- Also save `EXPORT/best_road_crack_yolov8s.pt` and `EXPORT/metrics_summary.txt` (mAP50, mAP50-95, precision, recall, per-class if available)
- Write `README_HANDOFF.md` with integrate steps for the other PC

---

## Phase 3 — Tune (before full train)

1. Run audit.
2. Run convert with defaults; inspect `_preview/` visually (describe what you see: tight vs loose boxes, noise).
3. If noisy tiny boxes: raise `min_area` (e.g. 60–100) and reconvert.
4. If boxes miss thin cracks: lower threshold slightly / reduce open kernel; reconvert once.
5. Build combined dataset.
6. Show me preview conclusions + combined class histogram. **If tuning looks bad, ask me before full train.**

---

## Phase 4 — Train

1. **Smoke** — must complete without path errors.
2. **Full** — yolov8s as specified. Do not stop early unless patience triggers or I interrupt.
3. If CUDA OOM: lower batch (16 → 12 → 8), keep imgsz 640 first.
4. Report final table:

| Metric | Value |
|--------|-------|
| mAP50 | |
| mAP50-95 | |
| Precision | |
| Recall | |
| Best epoch | |
| Weights path | EXPORT/best.pt |

---

## Phase 5 — Package for my other laptop (integration)

Create `EXPORT/` containing:

```text
EXPORT/
├── best.pt                          # primary — Flask loads this name
├── best_road_crack_yolov8s.pt       # dated/named backup
├── data_combined.yaml               # copy of yaml used
├── metrics_summary.txt
└── INTEGRATE.txt
```

`INTEGRATE.txt` must say exactly:

```text
On Archit PC (Road Inspection System):

1. Backup existing file if present:
   municipal_ai_system/app/ai_models/best.pt  →  best.pt.bak

2. Copy EXPORT/best.pt to:
   municipal_ai_system/app/ai_models/best.pt

3. In municipal_ai_system/.env ensure:
   YOLO_FORCE_MOCK=false
   YOLO_DEVICE=cpu
   (cpu is fine for inference on the non-GPU PC)

4. Restart Flask:
   cd municipal_ai_system
   venv\Scripts\python.exe run.py

5. Upload one inspection image and confirm boxes appear.
```

I will only need to copy the `EXPORT/` folder (or just `best.pt`) via USB/Drive.

---

## Quality bar (“best to best”)

- Do not use yolov8n for the **final** export.
- Do not train crack-only as the final Flask weights.
- Do not skip val.
- Do not use random wrong class IDs.
- Prefer longer train (80 + patience 20) over tiny 20-epoch toy runs.
- Keep all rare class-1 road samples.
- Show real metrics; do not claim success without numbers.

## First reply to me

1. Confirm you found `data/road` and crack masks + counts.  
2. Confirm you will create `RoadTrain4060/` layout.  
3. Paste the **plan** (phases + recipe + ETA).  
4. **Stop** until I approve.
