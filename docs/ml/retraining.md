# Retraining recipe — India road damage (YOLO)

Supersedes the earlier 4060 training prompts (removed).
Scope: **Indian roads only.** Every decision (gate, thresholds, promotion) is made on
RDD India val; other data is used only when it improves Indian-road accuracy.

> **Pothole labels fixed (2026-11-21).** The old `data/road` copy labelled faded
> lane paint / crosswalks as "pothole" and had no real potholes; the production
> `best.pt` learnt that. Training data is now `data/road_v2`, converted from the
> official RDD2022 India XML by class name: **2,236 real pothole boxes in train**
> (was 969 fake ones). Crack labels were already correct.
>
> Data sufficiency (same day): the current model scores mAP50 0.78 on training
> images vs 0.43 on val — it memorised a small set. `road_v2` fixes the pothole
> class; thin cracks still need more examples, best from your own ward photos (§5).

## Where we start (measured 2026-11-21, current `best.pt`, RDD India val, 1,178 images)

| | Precision | Recall | mAP50 |
|---|---|---|---|
| All classes | 0.62 | 0.41 | 0.43 |
| D00 longitudinal crack | 0.49 | 0.28 | 0.29 |
| D10 transverse crack | 0.82 | 0.27 | 0.35 (11 val boxes) |
| D20 alligator crack | 0.65 | 0.58 | 0.60 |
| D40 pothole | 0.52 | 0.51 | 0.50 |

What the old runs got wrong, and what the recipe changes:

| Problem | Fix in this recipe |
|---|---|
| 59% of India train images have no damage | backgrounds capped at 25% of train (`--bg-frac`) |
| Class 3 was lane paint, 0 real potholes | official XML converted by name: 2,236 real potholes |
| 50 transverse / 1,103 longitudinal boxes | image-level oversampling: D10 x4, D00 x2 (`--repeat`) |
| 448 px top-down crack close-ups, all forced to class D00 | removed from detection; road-pavement ones train a separate crack-outline model (§7) |
| Gate measured on a crack-inflated val (0.48) | gate on RDD India val only, A/B vs production |
| Single global conf 0.25 | per-class thresholds tuned on India val (F1.5, favours recall) |
| Inference size not tied to training | `best.model.json` carries `imgsz`; app uses it (`YOLO_IMGSZ=auto`) |
| AMP NaN / zero-mAP runs wasted hours | AMP off by default + collapse guard stops the run |

## Where to train

| Machine | Time for the `laptop` profile | Notes |
|---|---|---|
| **Kaggle free GPU** (T4 / P100) | ~5–8 h | recommended — free, runs in the background (12 h session) |
| RTX 4060 laptop | ~5–7 h | AMP off on that machine |
| Dev PC (MSI, i7-13700H, Intel Iris Xe — no NVIDIA GPU) | ~2.4 h **per epoch** → 10–15 days | measured 2026-11-21: 2.1 s/image on CPU; not practical |

## Train on Kaggle (free GPU) — step by step

1. **Package** (on this PC, project root):
   `python scripts/training/package_kaggle.py`
   → `artifacts/kaggle/road-inspection-india.zip` (≈545 MB: `road_v2`, the training
   scripts, current `best.pt` as baseline) and `artifacts/kaggle/kaggle_train.ipynb`.
2. **Upload the data:** kaggle.com → *Datasets* → *New Dataset* → drop the zip →
   keep it *Private* → *Create*.
3. **Import the notebook:** *Code* → *New Notebook* → *File* → *Import Notebook* →
   choose `kaggle_train.ipynb`.
4. **Notebook settings:** *Accelerator* **GPU T4 x2** (or P100), *Internet* **On**
   (needs a phone-verified account), *Add Input* → your dataset.
5. **Run in the background:** *Save Version* → **Save & Run All (Commit)**. You can
   close the browser; training stops within the 10.5 h budget and keeps the best epoch.
6. **Download:** open the finished version → *Output* → `india_model.zip`
   (best weights, curves, `evaluation/evaluation.md` comparing it with the old model).
7. **Promote on this PC:** unzip into `artifacts/runs/` so you have
   `artifacts/runs/india_laptop/weights/best.pt`, then
   `python scripts/training/evaluate.py --candidate artifacts/runs/india_laptop/weights/best.pt --device cpu --promote`
   (≈15 min on CPU). Restart the app — it now uses the new model and thresholds.

Kaggle gives a limited number of free GPU hours per week — check your quota on the
notebook editor's right panel before starting. For the larger `best` model set
`PROFILE = "best"` in the notebook's first code cell (needs ~2.5× the time; split
across two sessions with `--resume`, or accept the 10.5 h budget's result).

## 0. Setup on the RTX 4060 laptop

```bat
cd "C:\Road Inspection System"   &REM project root
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Copy the repo, then fetch and convert the official India data (≈425 MB, India only):

```bat
python scripts\training\fetch_rdd2022_india.py --out data\rdd2022_india
python -I scripts\training\convert_rdd_xml.py
```

This writes `data\road_v2\{train,val,test}\{images,labels}`. (Or copy `data\rdd2022_india`
and `data\road_v2` from the PC where they were already built.)

## 1. Build the dataset

```bat
python scripts\training\build_dataset.py --out data\build\india_v3
```

Prints a JSON report (image/box counts before and after oversampling, cleaning stats)
and writes `data\build\india_v3\{data.yaml, train.txt, build_report.json}`. Images are
hard-linked, so it needs almost no extra disk. Add `--dry-run` to only see the numbers.

## 2. Train

```bat
python scripts\training\train.py --data data\build\india_v3\data.yaml --profile laptop
```

| Profile | Model | imgsz | Epochs | Batch | Rough time on a 4060 laptop, AMP off |
|---|---|---|---|---|---|
| `laptop` | yolo11s | 768 | 150 (patience 40) | 16 | 6–9 h |
| `best` | yolo11m | 768 | 150 (patience 40) | 8 | 15–20 h |

Times are estimates — check the first epoch's duration and extrapolate.
CUDA out of memory → `--batch 8` (or 6). Run `--dry-run` to print the exact config.
`--model yolo26s.pt` tries the newer YOLO26 family with the same recipe.

Runs land in `artifacts\runs\<name>\` (curves, confusion matrix, `run_card.json`).
Resume an interrupted run: `python scripts\training\train.py --data ... --resume artifacts\runs\<name>\weights\last.pt`.

## 3. Evaluate and promote

```bat
python scripts\training\evaluate.py --candidate artifacts\runs\<name>\weights\best.pt --data data\build\india_v3\data.yaml
```

Validates candidate and production `best.pt` on India val (and India test for the
record), prints a per-class table, tunes thresholds and applies the gate:

* India val mAP50 ≥ production
* pothole AP50 ≥ production − 0.02
* mean recall ≥ production − 0.02
* local val mAP50 ≥ production (only if you added your own data, see §5)

If it passes, re-run with `--promote`: the current `best.pt` is backed up as
`best_backup_<timestamp>.pt`, the candidate is copied in, and `best.model.json` is
written beside it. Report: `artifacts\runs\<name>\evaluation\evaluation.md`.

## 4. Deploy to the app machine

Copy **both** `artifacts\ai_models\best.pt` and `best.model.json`.
In `.env` keep `YOLO_IMGSZ=auto` and `YOLO_FORCE_MOCK=false`, then restart Flask.
`YOLO_CLASS_THRESHOLDS=off` reverts to the single `YOLO_CONFIDENCE` if needed.

## 5. Adding more Indian data (the biggest lever)

RDD India has only ~2,200 damaged training images, and they are dashcam frames. Your
field workers shoot handheld phone photos — different angle and scale. **500–1,000
labelled photos from your own wards** will help more than any hyperparameter.

1. Collect across wards, road types (arterial, colony lanes, concrete), dry and
   monsoon conditions, morning/noon/evening light. Include some photos with no damage.
2. Label in Roboflow or CVAT with boxes, one class per box:
   * **pothole** — the whole hole, edge to edge
   * **longitudinal crack** — along the direction of travel; one box per continuous crack
   * **transverse crack** — across the road
   * **alligator crack** — box the whole interconnected patch, not individual cells
3. Export **YOLO format** into `data\india_extra\<source_name>\` with `data.yaml`,
   `train\` and `valid\` folders. Keep ~15% in `valid` — it becomes `local_val`
   and gets its own gate check.
4. Class names are mapped automatically (pothole / alligator / longitudinal /
   transverse). Ambiguous names are dropped and reported — map them explicitly,
   e.g. `--class-map crack=D00`.
5. Rebuild (step 1) — extra train images are repeated x3 (`--extra-repeat`) and any
   image duplicating val/test is removed automatically.

Public Indian pothole/crack sets (Roboflow Universe, Kaggle) can go in the same
folder. Check that they were captured in India, look at a sample of labels, and
check the licence before using them.

## 6. Optional: pretrain on other RDD countries, then fine-tune on India

Use this only if India-only training stalls below the gate. It needs the full
`RDD_SPLIT` folder (shared Drive) on the laptop.

```bat
python scripts\training\build_dataset.py --out data\build\india_v3 --pretrain-src "G:\My Drive\RDD_SPLIT"
python scripts\training\train.py --data data\build\india_v3_pretrain\data.yaml --profile pretrain
python scripts\training\train.py --data data\build\india_v3\data.yaml --profile finetune --weights artifacts\runs\<pretrain_run>\weights\best.pt
python scripts\training\evaluate.py --candidate artifacts\runs\<finetune_run>\weights\best.pt
```

The pretraining set excludes India images and China drone (top-down) images, and is
validated on India val throughout. Only the India fine-tune can be promoted, and only
through the same gate.

## Safety rails

* AMP is off unless you pass `--amp`. A collapse guard stops the run on NaN loss or
  val mAP50 = 0 after epoch 5; never resume from such a run.
* The RDD India val/test splits are never modified, so every run is comparable with
  today's baseline.
* `--promote` refuses to overwrite production when the gate fails (`--force` overrides).

## 7. Crack outlines (segmentation) — train later

A second, small model outlines crack pixels **inside crack boxes found by the
detector**, giving crack length/width/area for severity and cost. Crops of a road
photo look like close-up pavement images, so the public crack-mask data fits here
(it did not fit the detector).

```bat
python scripts\training\build_crack_seg.py --out data\build\crack_seg
python scripts\training\train.py --data data\build\crack_seg\data.yaml --profile crack_seg
```

* Data: 3,580 train / 668 val road-pavement crack images (CRACK500, GAPS384,
  CrackTree200, CFD, AEL; duplicates removed) + 500 crack-free Indian road crops.
* Profile: yolo11s-seg @ 448, 120 epochs, no mixup, flips/rotation on.
* Integration (after training): the app currently runs segmentation on the whole
  photo (`SEGMENTATION_ENABLED`); this model should instead run on each detected
  crack box crop. That wiring is a follow-up once weights exist.

