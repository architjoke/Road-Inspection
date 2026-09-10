"""Generate Colab-ready notebooks. Run from repo root: python notebooks/generate_notebooks.py"""
from __future__ import annotations

import json
from pathlib import Path


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip() + "\n"}


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.strip() + "\n",
    }


def notebook(cells: list[dict]) -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            "accelerator": "GPU",
            "colab": {"gpuType": "T4", "provenance": []},
        },
        "cells": cells,
    }


def write_nb(path: Path, cells: list[dict]) -> None:
    path.write_text(json.dumps(notebook(cells), indent=1), encoding="utf-8")
    print(f"Wrote {path}")


PART_A = [
    md("""# PART A — RDD2022 + YOLOv8 (Colab T4)

This notebook fine-tunes **pretrained YOLOv8** on **RDD2022** for four classes:

| id | code | name |
|----|------|------|
| 0 | D00 | Longitudinal Crack |
| 1 | D10 | Transverse Crack |
| 2 | D20 | Alligator Crack |
| 3 | D40 | Pothole |

**You already have the unzipped dataset.** Set `RDD2022_ROOT` in Block 4 to that folder. Do not merge this dataset with the crack-segmentation dataset.

Runtime: **Runtime → Change runtime type → T4 GPU**."""),
    md("## BLOCK 1 — Install packages"),
    code("""%pip install -q ultralytics==8.3.40 opencv-python-headless==10.0.1.1 matplotlib==3.9.2 pyyaml==6.0.2 boto3==1.35.36 pillow==10.4.0
# opencv-python-headless pin above can fail on Colab; fall back:
%pip install -q ultralytics opencv-python-headless matplotlib pyyaml boto3 Pillow"""),
    md("## BLOCK 2 — GPU check"),
    code("""import torch

print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("Device:", torch.cuda.get_device_name(0))
    print("Capability:", torch.cuda.get_device_capability(0))
else:
    print("No GPU. In Colab: Runtime → Change runtime type → T4 GPU, then Runtime → Restart session.")"""),
    md("## BLOCK 3 — Imports"),
    code("""from __future__ import annotations

import json
import os
import random
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image, UnidentifiedImageError
from ultralytics import YOLO

RANDOM_SEED = 42
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)

CLASS_TO_ID = {"D00": 0, "D10": 1, "D20": 2, "D40": 3}
ID_TO_CLASS = {v: k for k, v in CLASS_TO_ID.items()}
CLASS_NAMES = ["Longitudinal Crack", "Transverse Crack", "Alligator Crack", "Pothole"]
print("Primary classes:", CLASS_TO_ID)"""),
    md("""## BLOCK 4 — Point to the unzipped RDD2022 dataset

Skip Kaggle/download if the folder is already on Google Drive or uploaded to Colab.

Typical unzipped layouts (this notebook inspects whichever you have):

```text
RDD2022/
  Japan/train/images + annotations/xmls
  India/train/...
  Czech/train/...
  China_Drone/ ...
  United_States/ ...
  Norway/ ...
```"""),
    code("""from google.colab import drive

drive.mount("/content/drive")

# --- EDIT THIS PATH to your unzipped RDD2022 folder ---
RDD2022_ROOT = Path("/content/drive/MyDrive/datasets/RDD2022")

# Optional: if you still have a zip and want to extract it, set the zip path.
# Leave as None if already unzipped.
RDD2022_ZIP = None  # Path("/content/drive/MyDrive/datasets/RDD2022.zip")
EXTRACT_DIR = Path("/content/datasets")
EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

print("RDD2022_ROOT exists:", RDD2022_ROOT.exists(), "→", RDD2022_ROOT)
if not RDD2022_ROOT.exists():
    print("Folder not found. Update RDD2022_ROOT. Listing Drive datasets if present:")
    ds = Path("/content/drive/MyDrive/datasets")
    if ds.exists():
        for p in sorted(ds.iterdir()):
            print(" ", p)"""),
    md("## BLOCK 5 — Extract dataset (only if you still have a zip)"),
    code("""if RDD2022_ZIP is not None:
    zip_path = Path(RDD2022_ZIP)
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    dest = EXTRACT_DIR / "RDD2022"
    dest.mkdir(parents=True, exist_ok=True)
    print("Extracting", zip_path, "→", dest)
    shutil.unpack_archive(str(zip_path), str(dest))
    # If extract created a nested folder, prefer that as root
    nested = list(dest.glob("**/Japan")) + list(dest.glob("**/India"))
    RDD2022_ROOT = dest
    print("Extract done. Set RDD2022_ROOT =", RDD2022_ROOT)
else:
    print("Already unzipped — skipping extract. Using", RDD2022_ROOT)"""),
    md("## BLOCK 6 — Inspect dataset (do not hard-code country folders until this runs)"),
    code("""def list_tree(root: Path, max_depth: int = 3, max_entries: int = 80) -> None:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        indent = "  " * depth
        print(f"{indent}{Path(dirpath).name}/  ({len(filenames)} files, {len(dirnames)} dirs)")
        sample = filenames[:8]
        for name in sample:
            print(f"{indent}  - {name}")
        if len(filenames) > 8:
            print(f"{indent}  ... {len(filenames) - 8} more files")
        count += 1
        if count >= max_entries:
            print("... truncated listing")
            break

if not RDD2022_ROOT.exists():
    raise FileNotFoundError(f"Set RDD2022_ROOT to your unzipped dataset. Current: {RDD2022_ROOT}")

print("=== Directory structure ===")
list_tree(RDD2022_ROOT)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
ann_exts = Counter()
img_exts = Counter()
xml_sample = None
txt_sample = None
json_sample = None
n_img, n_xml, n_txt, n_json = 0, 0, 0, 0
for p in RDD2022_ROOT.rglob("*"):
    if not p.is_file():
        continue
    suf = p.suffix.lower()
    if suf in IMAGE_EXTS:
        img_exts[suf] += 1
        n_img += 1
    elif suf == ".xml":
        n_xml += 1
        ann_exts[suf] += 1
        if xml_sample is None:
            xml_sample = p
    elif suf == ".txt":
        n_txt += 1
        ann_exts[suf] += 1
        if txt_sample is None and p.name != "classes.txt":
            txt_sample = p
    elif suf == ".json":
        n_json += 1
        ann_exts[suf] += 1
        if json_sample is None:
            json_sample = p

print("\\n=== File counts ===")
print("images:", n_img, dict(img_exts))
print("xml:", n_xml, "txt:", n_txt, "json:", n_json)

print("\\n=== Annotation format sample ===")
if xml_sample:
    print("XML sample:", xml_sample)
    print(Path(xml_sample).read_text(encoding="utf-8", errors="replace")[:1500])
if txt_sample:
    print("TXT sample:", txt_sample)
    print(Path(txt_sample).read_text(encoding="utf-8", errors="replace")[:800])
if json_sample:
    print("JSON sample:", json_sample)
    print(Path(json_sample).read_text(encoding="utf-8", errors="replace")[:800])"""),
    md("## BLOCK 7 — Convert/prepare annotations to YOLO format"),
    code("""YOLO_DS = Path("/content/rdd2022_yolo")
IMAGES_OUT = YOLO_DS / "images"
LABELS_OUT = YOLO_DS / "labels"
for split in ("train", "val"):
    (IMAGES_OUT / split).mkdir(parents=True, exist_ok=True)
    (LABELS_OUT / split).mkdir(parents=True, exist_ok=True)

label_stats = Counter()
skipped_classes = Counter()
converted = 0
already_yolo = 0
xml_parsed = 0


def yolo_line(cls_id, xmin, ymin, xmax, ymax, w, h) -> str | None:
    if w <= 0 or h <= 0:
        return None
    bw, bh = xmax - xmin, ymax - ymin
    if bw <= 0 or bh <= 0:
        return None
    xc = (xmin + xmax) / 2.0 / w
    yc = (ymin + ymax) / 2.0 / h
    nw, nh = bw / w, bh / h
    if not (0 <= xc <= 1 and 0 <= yc <= 1 and 0 < nw <= 1 and 0 < nh <= 1):
        xc, yc = min(max(xc, 0), 1), min(max(yc, 0), 1)
        nw, nh = min(max(nw, 1e-6), 1), min(max(nh, 1e-6), 1)
    return f"{cls_id} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}"


def parse_voc_xml(xml_path: Path) -> tuple[Path | None, list[str]]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    filename = root.findtext("filename")
    size = root.find("size")
    w = int(size.findtext("width")) if size is not None and size.findtext("width") else None
    h = int(size.findtext("height")) if size is not None and size.findtext("height") else None
    folder = xml_path.parent
    img_path = None
    candidates = []
    if filename:
        candidates.append(folder / filename)
        candidates.append(folder.parent / "images" / filename)
        candidates.append(folder.parent.parent / "images" / filename)
        stem = Path(filename).stem
    else:
        stem = xml_path.stem
    for ext in (".jpg", ".jpeg", ".png", ".JPG", ".PNG"):
        candidates.append(folder / f"{stem}{ext}")
        candidates.append(folder.parent / "images" / f"{stem}{ext}")
        candidates.append(xml_path.parent.parent / "images" / f"{stem}{ext}")
        # RDD2022 common: Country/train/images and Country/train/annotations/xmls
        candidates.append(xml_path.parent.parent.parent / "images" / f"{stem}{ext}")
        candidates.append(xml_path.parents[1] / "images" / f"{stem}{ext}")
    for c in candidates:
        if c.exists() and c.is_file():
            img_path = c
            break
    if img_path is None:
        hits = list(xml_path.parents[2].rglob(f"{xml_path.stem}.*")) if len(xml_path.parents) >= 2 else []
        hits = [h for h in hits if h.suffix.lower() in IMAGE_EXTS]
        img_path = hits[0] if hits else None
    if img_path is not None and (w is None or h is None or w == 0 or h == 0):
        im = cv2.imread(str(img_path))
        if im is None:
            return img_path, []
        h, w = im.shape[:2]
    lines = []
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip()
        if name not in CLASS_TO_ID:
            skipped_classes[name] += 1
            continue
        bb = obj.find("bndbox")
        if bb is None:
            continue
        xmin = float(bb.findtext("xmin"))
        ymin = float(bb.findtext("ymin"))
        xmax = float(bb.findtext("xmax"))
        ymax = float(bb.findtext("ymax"))
        line = yolo_line(CLASS_TO_ID[name], xmin, ymin, xmax, ymax, w, h)
        if line:
            lines.append(line)
            label_stats[name] += 1
    return img_path, lines


def looks_like_yolo_txt(txt_path: Path) -> bool:
    try:
        raw = txt_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return False
    if not raw:
        return True
    parts = raw.split()
    try:
        vals = [float(x) for x in parts[:5]]
    except ValueError:
        return False
    return len(parts) % 5 == 0 and 0 <= vals[0] <= 20


# Prefer XML (RDD2022 default). If a country is already YOLO, copy those pairs.
xml_files = list(RDD2022_ROOT.rglob("*.xml"))
print("XML files found:", len(xml_files))

image_label_pairs: list[tuple[Path, Path]] = []

if xml_files:
    for xml_path in xml_files:
        try:
            img_path, lines = parse_voc_xml(xml_path)
            xml_parsed += 1
        except Exception as exc:
            print("XML parse failed:", xml_path, exc)
            continue
        if img_path is None or not img_path.exists():
            continue
        # Keep images even if they have zero of the 4 classes (background). Optional: skip empty.
        rel_id = f"{img_path.parent.parent.name}_{img_path.stem}"
        out_img = None  # filled after split
        image_label_pairs.append((img_path, lines, rel_id))
        converted += 1
else:
    print("No XML found. Searching for existing YOLO .txt labels next to images...")
    for img in RDD2022_ROOT.rglob("*"):
        if img.suffix.lower() not in IMAGE_EXTS:
            continue
        txt = img.with_suffix(".txt")
        if not txt.exists():
            alt = img.parent.parent / "labels" / f"{img.stem}.txt"
            txt = alt if alt.exists() else None
        if txt is None:
            continue
        if not looks_like_yolo_txt(txt):
            continue
        lines = []
        for line in txt.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            cls = int(float(line.split()[0]))
            # If labels already use 0-3 for D00-D40, keep; if they use other ids, skip unknown
            if cls not in ID_TO_CLASS:
                skipped_classes[str(cls)] += 1
                continue
            lines.append(line.strip())
            label_stats[ID_TO_CLASS[cls]] += 1
        image_label_pairs.append((img, lines, f"{img.parent.name}_{img.stem}"))
        already_yolo += 1

print("Pairs collected:", len(image_label_pairs), "xml_parsed:", xml_parsed, "already_yolo:", already_yolo)
print("Kept class boxes:", dict(label_stats))
print("Skipped non-target classes/ids (not invented, just ignored):", skipped_classes.most_common(20))

# Deduplicate by image path
uniq = {}
for item in image_label_pairs:
    uniq[str(item[0].resolve())] = item
pairs = list(uniq.values())
random.shuffle(pairs)
n_val = max(1, int(0.15 * len(pairs))) if pairs else 0
val_pairs = pairs[:n_val]
train_pairs = pairs[n_val:]
print("train:", len(train_pairs), "val:", len(val_pairs))


def export_split(split_name: str, items: list) -> None:
    for img_path, lines, rel_id in items:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in rel_id)
        dst_img = IMAGES_OUT / split_name / f"{safe}{img_path.suffix.lower()}"
        dst_lbl = LABELS_OUT / split_name / f"{safe}.txt"
        if not dst_img.exists():
            shutil.copy2(img_path, dst_img)
        dst_lbl.write_text("\\n".join(lines) + ("\\n" if lines else ""), encoding="utf-8")

export_split("train", train_pairs)
export_split("val", val_pairs)
print("YOLO dataset written to", YOLO_DS)"""),
    md("## BLOCK 8 — Create data.yaml"),
    code("""data_yaml = {
    "path": str(YOLO_DS),
    "train": "images/train",
    "val": "images/val",
    "names": {
        0: "D00_longitudinal_crack",
        1: "D10_transverse_crack",
        2: "D20_alligator_crack",
        3: "D40_pothole",
    },
    "nc": 4,
}
yaml_path = YOLO_DS / "data.yaml"
yaml_path.write_text(yaml.dump(data_yaml, sort_keys=False), encoding="utf-8")
print(yaml_path.read_text())"""),
    md("## BLOCK 9 — Validate dataset"),
    code("""def validate_yolo_split(split: str) -> dict:
    img_dir = IMAGES_OUT / split
    lbl_dir = LABELS_OUT / split
    images = sorted([p for p in img_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS])
    labels = {p.stem: p for p in lbl_dir.glob("*.txt")}
    missing_labels, missing_images, corrupt, bad_boxes = [], [], [], []
    empty_labels = 0
    for img in images:
        if img.stem not in labels:
            missing_labels.append(img.name)
            continue
        try:
            with Image.open(img) as im:
                im.verify()
            with Image.open(img) as im:
                w, h = im.size
        except (UnidentifiedImageError, OSError) as exc:
            corrupt.append((img.name, str(exc)))
            continue
        text = labels[img.stem].read_text(encoding="utf-8").strip()
        if not text:
            empty_labels += 1
            continue
        for i, line in enumerate(text.splitlines()):
            parts = line.split()
            if len(parts) != 5:
                bad_boxes.append((img.name, i, line, "expected 5 values"))
                continue
            cls, xc, yc, nw, nh = int(float(parts[0])), *map(float, parts[1:])
            if cls not in ID_TO_CLASS:
                bad_boxes.append((img.name, i, line, "class not in 0-3"))
            if min(xc, yc, nw, nh) < 0 or max(xc, yc) > 1 or max(nw, nh) > 1:
                bad_boxes.append((img.name, i, line, "normalized box out of range"))
    for stem, lp in labels.items():
        if not any(stem == img.stem for img in images):
            missing_images.append(lp.name)
    report = {
        "split": split,
        "images": len(images),
        "labels": len(labels),
        "missing_labels": len(missing_labels),
        "missing_images": len(missing_images),
        "corrupt_images": len(corrupt),
        "invalid_boxes": len(bad_boxes),
        "empty_labels": empty_labels,
    }
    print(json.dumps(report, indent=2))
    if missing_labels[:5]:
        print("sample missing labels:", missing_labels[:5])
    if corrupt[:5]:
        print("sample corrupt:", corrupt[:5])
    if bad_boxes[:5]:
        print("sample bad boxes:", bad_boxes[:5])
    return report

train_report = validate_yolo_split("train")
val_report = validate_yolo_split("val")"""),
    md("## BLOCK 10 — Visualize samples + class distribution"),
    code("""def draw_yolo_boxes(img_path: Path, lbl_path: Path):
    img = cv2.imread(str(img_path))
    if img is None:
        return None
    h, w = img.shape[:2]
    vis = img.copy()
    text = lbl_path.read_text(encoding="utf-8").strip()
    colors = [(0, 255, 0), (255, 0, 0), (0, 165, 255), (0, 0, 255)]
    for line in text.splitlines():
        cls, xc, yc, nw, nh = [float(x) for x in line.split()]
        cls = int(cls)
        x1 = int((xc - nw / 2) * w)
        y1 = int((yc - nh / 2) * h)
        x2 = int((xc + nw / 2) * w)
        y2 = int((yc + nh / 2) * h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), colors[cls % 4], 2)
        cv2.putText(vis, ID_TO_CLASS[cls], (x1, max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colors[cls % 4], 2)
    return cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)

train_imgs = sorted((IMAGES_OUT / "train").glob("*"))[:12]
fig, axes = plt.subplots(3, 4, figsize=(16, 10))
for ax, img_path in zip(axes.ravel(), train_imgs):
    lbl = LABELS_OUT / "train" / f"{img_path.stem}.txt"
    vis = draw_yolo_boxes(img_path, lbl)
    ax.imshow(vis if vis is not None else np.zeros((64, 64, 3), dtype=np.uint8))
    ax.set_title(img_path.stem[:28], fontsize=8)
    ax.axis("off")
plt.tight_layout()
plt.show()

dist = Counter()
for split in ("train", "val"):
    for txt in (LABELS_OUT / split).glob("*.txt"):
        for line in txt.read_text(encoding="utf-8").splitlines():
            if line.strip():
                dist[ID_TO_CLASS[int(float(line.split()[0]))]] += 1
print("Class box counts:", dict(dist))
plt.figure(figsize=(7, 4))
plt.bar(list(dist.keys()), list(dist.values()))
plt.title("RDD2022 class distribution (boxes)")
plt.ylabel("count")
plt.show()"""),
    md("## BLOCK 11 — Load pretrained YOLOv8"),
    code("""DEVICE = 0 if torch.cuda.is_available() else "cpu"
# yolov8s is a practical T4 starting point; use yolov8n if you run out of memory.
PRETRAINED = "yolov8s.pt"
model = YOLO(PRETRAINED)
print("Loaded", PRETRAINED, "device:", DEVICE)"""),
    md("""## BLOCK 12 — Fine-tune YOLOv8 (T4)

Start from pretrained weights. Increase `epochs` after a smoke test (`epochs=3`)."""),
    code("""TRAIN_EPOCHS = 30  # raise to 50–100 after the pipeline works
IMG_SIZE = 640
BATCH = 8  # drop to 4 if CUDA OOM
PROJECT = "/content/runs/rdd2022"
RUN_NAME = "yolov8s_finetune"

train_results = model.train(
    data=str(yaml_path),
    epochs=TRAIN_EPOCHS,
    imgsz=IMG_SIZE,
    batch=BATCH,
    device=DEVICE,
    project=PROJECT,
    name=RUN_NAME,
    exist_ok=True,
    pretrained=True,
    workers=2,
    patience=8,
    amp=True,
    seed=RANDOM_SEED,
)
print(train_results)"""),
    md("## BLOCK 13 — Evaluate (precision, recall, mAP50, mAP50-95, confusion matrix)"),
    code("""best_pt = Path(PROJECT) / RUN_NAME / "weights" / "best.pt"
if not best_pt.exists():
    # Ultralytics may write under /content/runs/detect/...
    cands = list(Path("/content").rglob("best.pt"))
    print("best.pt candidates:", cands)
    best_pt = cands[0] if cands else best_pt
print("Using", best_pt)

eval_model = YOLO(str(best_pt))
metrics = eval_model.val(data=str(yaml_path), device=DEVICE, plots=True)
print("Precision:", float(metrics.box.mp))
print("Recall:", float(metrics.box.mr))
print("mAP50:", float(metrics.box.map50))
print("mAP50-95:", float(metrics.box.map))
print("Per-class mAP50:", list(map(float, metrics.box.ap50)))
cm = Path(eval_model.trainer.save_dir) if hasattr(eval_model, "trainer") else None
for p in Path("/content").rglob("confusion_matrix*.png"):
    print("confusion matrix plot:", p)"""),
    md("## BLOCK 14 — Test predictions on sample images"),
    code("""pred_model = YOLO(str(best_pt))
sample_dir = IMAGES_OUT / "val"
samples = sorted(sample_dir.glob("*"))[:8]
pred_dir = Path("/content/yolo_pred_samples")
pred_dir.mkdir(exist_ok=True)
results = pred_model.predict(
    source=[str(p) for p in samples],
    device=DEVICE,
    save=True,
    project=str(pred_dir),
    name="val_samples",
    exist_ok=True,
    conf=0.25,
)
for r in results:
    print(Path(r.path).name, [(ID_TO_CLASS.get(int(c), int(c)), round(float(s), 3)) for c, s in zip(r.boxes.cls, r.boxes.conf)] if r.boxes is not None else [])"""),
    md("## BLOCK 15 — Save best.pt"),
    code("""export_dir = Path("/content/export/yolov8")
export_dir.mkdir(parents=True, exist_ok=True)
saved_best = export_dir / "best.pt"
shutil.copy2(best_pt, saved_best)
print("Saved", saved_best, "size_mb:", round(saved_best.stat().st_size / 1e6, 2))"""),
    md("## BLOCK 16 — Download best.pt from Colab"),
    code("""from google.colab import files

files.download(str(saved_best))"""),
    md("""## BLOCK 17 — Upload best.pt to Amazon S3

Do **not** paste access keys into the notebook. In Colab: the key icon (Secrets) → add `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `S3_BUCKET`."""),
    code("""%pip install -q boto3
import boto3
from google.colab import userdata

os.environ["AWS_ACCESS_KEY_ID"] = userdata.get("AWS_ACCESS_KEY_ID")
os.environ["AWS_SECRET_ACCESS_KEY"] = userdata.get("AWS_SECRET_ACCESS_KEY")
os.environ["AWS_DEFAULT_REGION"] = userdata.get("AWS_DEFAULT_REGION")
S3_BUCKET = userdata.get("S3_BUCKET")

s3 = boto3.client("s3", region_name=os.environ["AWS_DEFAULT_REGION"])
key = "models/yolov8/best.pt"
s3.upload_file(str(saved_best), S3_BUCKET, key)
print(f"Uploaded s3://{S3_BUCKET}/{key}")

listed = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="models/yolov8/")
for obj in listed.get("Contents", []):
    print(obj["Key"], obj["Size"])"""),
]

PART_B = [
    md("""# PART B — Merged Crack Segmentation Dataset + SegFormer (Colab T4)

Binary masks only:

- `0` background
- `1` crack

Do **not** convert masks to YOLO boxes. Do **not** mix this data into RDD2022.

**You already unzipped the dataset.** Set `CRACK_ROOT` in Block 4.

Kaggle source (reference only): https://www.kaggle.com/datasets/lakshaymiddha/crack-segmentation-dataset"""),
    md("## BLOCK 1 — Install packages"),
    code("""%pip install -q transformers==4.44.2 datasets==3.0.1 accelerate==0.34.2 evaluate==0.4.3 safetensors
%pip install -q opencv-python-headless matplotlib scikit-learn boto3 Pillow tqdm"""),
    md("## BLOCK 2 — GPU check"),
    code("""import torch

print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))
else:
    print("Enable T4 GPU in Colab Runtime settings.")"""),
    md("## BLOCK 3 — Imports"),
    code("""from __future__ import annotations

import os
import random
import shutil
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, UnidentifiedImageError
from sklearn.model_selection import train_test_split
from tqdm.auto import tqdm

RANDOM_SEED = 42
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)"""),
    md("""## BLOCK 4 — Point to unzipped crack dataset (skip download if already extracted)

If you still need to download from Kaggle, store `KAGGLE_USERNAME` and `KAGGLE_KEY` in Colab Secrets and run the optional cell comments."""),
    code("""from google.colab import drive

drive.mount("/content/drive")

# --- EDIT to your unzipped crack-segmentation folder ---
CRACK_ROOT = Path("/content/drive/MyDrive/datasets/crack-segmentation-dataset")
CRACK_ZIP = None  # Path("/content/drive/MyDrive/datasets/crack-segmentation-dataset.zip")

print("CRACK_ROOT exists:", CRACK_ROOT.exists(), "→", CRACK_ROOT)
if not CRACK_ROOT.exists():
    ds = Path("/content/drive/MyDrive/datasets")
    if ds.exists():
        for p in sorted(ds.iterdir()):
            print(" ", p)

# Optional Kaggle download (commented). Uncomment only if you do not already have files.
# from google.colab import userdata
# import os
# os.environ["KAGGLE_USERNAME"] = userdata.get("KAGGLE_USERNAME")
# os.environ["KAGGLE_KEY"] = userdata.get("KAGGLE_KEY")
# !kaggle datasets download -d lakshaymiddha/crack-segmentation-dataset -p /content/data
# !unzip -q /content/data/crack-segmentation-dataset.zip -d /content/data/crack"""),
    md("## BLOCK 5 — Extract (only if you still have a zip)"),
    code("""if CRACK_ZIP is not None:
    dest = Path("/content/data/crack")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.unpack_archive(str(CRACK_ZIP), str(dest))
    CRACK_ROOT = dest
    print("Extracted to", CRACK_ROOT)
else:
    print("Using existing unzipped folder", CRACK_ROOT)"""),
    md("## BLOCK 6 — Inspect dataset and identify images vs masks"),
    code("""def list_tree(root: Path, max_depth: int = 4, max_entries: int = 60) -> None:
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        print("  " * depth + f"{Path(dirpath).name}/ ({len(filenames)} files)")
        n += 1
        if n >= max_entries:
            print("... truncated")
            break

if not CRACK_ROOT.exists():
    raise FileNotFoundError(CRACK_ROOT)
list_tree(CRACK_ROOT)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
all_files = [p for p in CRACK_ROOT.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
print("image-like files:", len(all_files))

# Heuristic: folders named images/masks or img/mask or train/test
for key in ("images", "masks", "mask", "image", "train", "test", "val"):
    hits = [p for p in CRACK_ROOT.rglob("*") if p.is_dir() and p.name.lower() == key]
    if hits:
        print("dir named", key, "→", hits[:8])"""),
    md("## BLOCK 7 — Verify image/mask pairs"),
    code("""def discover_pairs(root: Path) -> list[tuple[Path, Path]]:
    pairs = []
    # Common Kaggle layout: **/train/images + **/train/masks, **/test/images + **/test/masks
    image_dirs = [p for p in root.rglob("*") if p.is_dir() and p.name.lower() in {"images", "image", "imgs"}]
    for img_dir in image_dirs:
        mask_dir = None
        for cand_name in ("masks", "mask", "masks_png", "annotated"):
            c = img_dir.parent / cand_name
            if c.exists():
                mask_dir = c
                break
        if mask_dir is None:
            continue
        print("Pairing", img_dir, "<->", mask_dir)
        masks_by_stem = {}
        for m in mask_dir.iterdir():
            if m.is_file() and m.suffix.lower() in IMAGE_EXTS:
                masks_by_stem[m.stem.replace("_mask", "")] = m
        for im in img_dir.iterdir():
            if not im.is_file() or im.suffix.lower() not in IMAGE_EXTS:
                continue
            stem = im.stem.replace("_orig", "")
            mask = masks_by_stem.get(stem) or masks_by_stem.get(im.stem)
            if mask:
                pairs.append((im, mask))
    # Fallback: same stem in sibling folders
    if not pairs:
        print("Standard images/masks folders not found; trying stem matching across the tree.")
        by_stem = {}
        for p in all_files:
            by_stem.setdefault(p.stem, []).append(p)
        for stem, files in by_stem.items():
            if len(files) < 2:
                continue
            imgs = [f for f in files if "mask" not in f.name.lower() and "mask" not in str(f.parent).lower()]
            masks = [f for f in files if "mask" in f.name.lower() or "mask" in str(f.parent).lower()]
            if imgs and masks:
                pairs.append((imgs[0], masks[0]))
    return pairs

pairs = discover_pairs(CRACK_ROOT)
print("paired files:", len(pairs))
if not pairs:
    raise RuntimeError("Could not pair images and masks. Inspect Block 6 listing and adjust discover_pairs().")

ok, dim_mismatch, corrupt, unique_mask_vals = [], [], [], set()
for im_path, mask_path in tqdm(pairs, desc="verify"):
    try:
        with Image.open(im_path) as im:
            im.verify()
        with Image.open(im_path) as im:
            iw, ih = im.size
            im_mode = im.mode
        with Image.open(mask_path) as mk:
            mk.verify()
        with Image.open(mask_path) as mk:
            mw, mh = mk.size
            arr = np.array(mk)
    except (UnidentifiedImageError, OSError) as exc:
        corrupt.append((im_path.name, str(exc)))
        continue
    if (iw, ih) != (mw, mh):
        dim_mismatch.append((im_path.name, (iw, ih), (mw, mh)))
        continue
    unique_mask_vals.update(np.unique(arr).tolist()[:12])
    ok.append((im_path, mask_path))

print("valid pairs:", len(ok), "corrupt:", len(corrupt), "dim mismatch:", len(dim_mismatch))
print("sample unique mask values (first pass):", sorted(list(unique_mask_vals))[:20])
print("sample corrupt:", corrupt[:5])
print("sample dim mismatch:", dim_mismatch[:5])
pairs = ok"""),
    md("""## BLOCK 8 — Train / val / test split (no leakage)

Split by **filename stem**, not by random crops of the same image. If the dataset already has train/test folders, we honor them and only split validation from train."""),
    code("""def split_source(path: Path) -> str:
    parts = {x.lower() for x in path.parts}
    if "test" in parts:
        return "test"
    if "val" in parts or "valid" in parts or "validation" in parts:
        return "val"
    if "train" in parts:
        return "train"
    return "unspecified"

grouped = {"train": [], "val": [], "test": [], "unspecified": []}
for item in pairs:
    grouped[split_source(item[0])].append(item)

print({k: len(v) for k, v in grouped.items()})

if grouped["unspecified"] and not grouped["train"]:
    stems = [p[0].stem for p in grouped["unspecified"]]
    if len(stems) != len(set(stems)):
        print("Warning: duplicate stems exist; splitting by unique stems to reduce leakage.")
    unique_stems = sorted(set(stems))
    train_s, temp_s = train_test_split(unique_stems, test_size=0.30, random_state=RANDOM_SEED)
    val_s, test_s = train_test_split(temp_s, test_size=0.50, random_state=RANDOM_SEED)
    by_stem = {}
    for im, mk in grouped["unspecified"]:
        by_stem.setdefault(im.stem, []).append((im, mk))
    train_pairs = [x for s in train_s for x in by_stem[s]]
    val_pairs = [x for s in val_s for x in by_stem[s]]
    test_pairs = [x for s in test_s for x in by_stem[s]]
else:
    train_pairs = grouped["train"]
    test_pairs = grouped["test"]
    val_pairs = grouped["val"]
    if not val_pairs and train_pairs:
        train_stems = sorted({p[0].stem for p in train_pairs})
        tr, va = train_test_split(train_stems, test_size=0.15, random_state=RANDOM_SEED)
        tr_set, va_set = set(tr), set(va)
        val_pairs = [p for p in train_pairs if p[0].stem in va_set]
        train_pairs = [p for p in train_pairs if p[0].stem in tr_set]
    if not test_pairs:
        test_pairs = val_pairs[: max(1, len(val_pairs) // 5)]

overlap = set(p[0].stem for p in train_pairs) & set(p[0].stem for p in val_pairs)
overlap |= set(p[0].stem for p in train_pairs) & set(p[0].stem for p in test_pairs)
print("train/val/test:", len(train_pairs), len(val_pairs), len(test_pairs))
print("stem overlap train∩val/test (should be empty):", overlap)"""),
    md("## BLOCK 9 — Visualize images and ground-truth masks"),
    code("""fig, axes = plt.subplots(4, 2, figsize=(8, 14))
for i, (im_path, mk_path) in enumerate(train_pairs[:4]):
    img = np.array(Image.open(im_path).convert("RGB"))
    mask = np.array(Image.open(mk_path))
    if mask.ndim == 3:
        mask = mask[..., 0]
    axes[i, 0].imshow(img)
    axes[i, 0].set_title(im_path.name[:40])
    axes[i, 0].axis("off")
    axes[i, 1].imshow(mask, cmap="gray")
    axes[i, 1].set_title("mask unique=" + str(np.unique(mask)[:8]))
    axes[i, 1].axis("off")
plt.tight_layout()
plt.show()"""),
    md("## BLOCK 10 — Load pretrained SegFormer (`num_labels = 2`)"),
    code("""from torch.utils.data import Dataset, DataLoader
from transformers import (
    SegformerForSemanticSegmentation,
    SegformerImageProcessor,
    TrainingArguments,
    Trainer,
)

MODEL_NAME = "nvidia/mit-b0"  # compact; fits T4. Try mit-b2 later if VRAM allows.
NUM_LABELS = 2
IMAGE_SIZE = 512

processor = SegformerImageProcessor.from_pretrained(
    MODEL_NAME,
    do_reduce_labels=False,
    size={"height": IMAGE_SIZE, "width": IMAGE_SIZE},
)

id2label = {0: "background", 1: "crack"}
label2id = {"background": 0, "crack": 1}

model = SegformerForSemanticSegmentation.from_pretrained(
    MODEL_NAME,
    num_labels=NUM_LABELS,
    id2label=id2label,
    label2id=label2id,
    ignore_mismatched_sizes=True,
)
print(model.config.num_labels, model.config.model_type)


def binarize_mask(mask: np.ndarray) -> np.ndarray:
    if mask.ndim == 3:
        mask = mask[..., 0]
    # Dataset masks may be {0,1}, {0,255}, or indexed colors. Do not invent extra classes.
    if mask.max() > 1:
        mask = (mask > 127).astype(np.uint8)
    else:
        mask = (mask > 0).astype(np.uint8)
    return mask


class CrackSegDataset(Dataset):
    def __init__(self, items):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        im_path, mk_path = self.items[idx]
        image = Image.open(im_path).convert("RGB")
        mask = binarize_mask(np.array(Image.open(mk_path)))
        encoded = processor(images=image, segmentation_maps=Image.fromarray(mask), return_tensors="pt")
        item = {k: v.squeeze(0) for k, v in encoded.items()}
        return item

train_ds = CrackSegDataset(train_pairs)
val_ds = CrackSegDataset(val_pairs)
test_ds = CrackSegDataset(test_pairs)
print(len(train_ds), len(val_ds), len(test_ds))"""),
    md("## BLOCK 11 — Fine-tune SegFormer on T4"),
    code("""import evaluate
import torch.nn.functional as F

mean_iou = evaluate.load("mean_iou")


def collate_fn(batch):
    pixel_values = torch.stack([b["pixel_values"] for b in batch])
    labels = torch.stack([b["labels"] for b in batch])
    return {"pixel_values": pixel_values, "labels": labels}


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    logits = torch.tensor(logits)
    labels = torch.tensor(labels)
    logits = F.interpolate(logits, size=labels.shape[-2:], mode="bilinear", align_corners=False)
    preds = logits.argmax(dim=1).numpy()
    labels_np = labels.numpy()
    metrics = mean_iou.compute(
        predictions=preds,
        references=labels_np,
        num_labels=NUM_LABELS,
        ignore_index=255,
        reduce_labels=False,
    )
    return {"mean_iou": metrics["mean_iou"], "mean_accuracy": metrics.get("mean_accuracy")}

EPOCHS = 8
BATCH = 4  # 2 if OOM
out_dir = "/content/segformer_cracks"

args = TrainingArguments(
    output_dir=out_dir,
    learning_rate=6e-5,
    num_train_epochs=EPOCHS,
    per_device_train_batch_size=BATCH,
    per_device_eval_batch_size=BATCH,
    warmup_ratio=0.1,
    weight_decay=0.01,
    eval_strategy="epoch",
    save_strategy="epoch",
    logging_steps=50,
    load_best_model_at_end=True,
    metric_for_best_model="mean_iou",
    greater_is_better=True,
    fp16=torch.cuda.is_available(),
    report_to=[],
    remove_unused_columns=False,
    dataloader_num_workers=0,
)

trainer = Trainer(
    model=model,
    args=args,
    train_dataset=train_ds,
    eval_dataset=val_ds,
    compute_metrics=compute_metrics,
    data_collator=collate_fn,
)
train_out = trainer.train()
print(train_out)"""),
    md("## BLOCK 12 — Evaluate IoU, mIoU, Dice, precision, recall"),
    code("""def segmentation_scores(items, max_n=None):
    model.eval()
    device = model.device
    tp = fp = fn = tn = 0
    ious = []
    dices = []
    use = items if max_n is None else items[:max_n]
    for im_path, mk_path in tqdm(use, desc="eval"):
        image = Image.open(im_path).convert("RGB")
        gt = binarize_mask(np.array(Image.open(mk_path)))
        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = model(**inputs).logits
        up = F.interpolate(logits, size=gt.shape[-2:], mode="bilinear", align_corners=False)
        pred = up.argmax(dim=1)[0].cpu().numpy().astype(np.uint8)
        pred = (pred == 1).astype(np.uint8)
        gt_b = (gt == 1).astype(np.uint8)
        tp += int(((pred == 1) & (gt_b == 1)).sum())
        fp += int(((pred == 1) & (gt_b == 0)).sum())
        fn += int(((pred == 0) & (gt_b == 1)).sum())
        tn += int(((pred == 0) & (gt_b == 0)).sum())
        inter = tp  # running totals used at end; per-image:
        inter_i = int(((pred == 1) & (gt_b == 1)).sum())
        union_i = int(((pred == 1) | (gt_b == 1)).sum())
        ious.append(inter_i / union_i if union_i else 1.0)
        den = 2 * inter_i + (pred.sum() + gt_b.sum() - 2 * inter_i)
        # Dice = 2*inter / (pred + gt)
        d = (2 * inter_i) / (pred.sum() + gt_b.sum() + 1e-8)
        dices.append(float(d))
    precision = tp / (tp + fp + 1e-8)
    recall = tp / (tp + fn + 1e-8)
    iou_fg = tp / (tp + fp + fn + 1e-8)
    iou_bg = tn / (tn + fp + fn + 1e-8)
    miou = 0.5 * (iou_fg + iou_bg)
    return {
        "IoU_crack": iou_fg,
        "IoU_background": iou_bg,
        "mIoU": miou,
        "Dice": float(np.mean(dices)),
        "Precision": precision,
        "Recall": recall,
        "mean_image_IoU": float(np.mean(ious)),
    }

print("val:", segmentation_scores(val_pairs, max_n=min(200, len(val_pairs))))
print("test:", segmentation_scores(test_pairs, max_n=min(200, len(test_pairs))))"""),
    md("## BLOCK 13 — Visualize original / ground-truth / prediction"),
    code("""device = model.device
fig, axes = plt.subplots(3, 3, figsize=(10, 10))
for i, (im_path, mk_path) in enumerate(val_pairs[:3]):
    image = Image.open(im_path).convert("RGB")
    gt = binarize_mask(np.array(Image.open(mk_path)))
    inputs = processor(images=image, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        logits = model(**inputs).logits
    up = torch.nn.functional.interpolate(logits, size=gt.shape[-2:], mode="bilinear", align_corners=False)
    pred = up.argmax(dim=1)[0].cpu().numpy()
    axes[i, 0].imshow(image)
    axes[i, 0].set_title("image")
    axes[i, 1].imshow(gt, cmap="gray")
    axes[i, 1].set_title("ground truth")
    axes[i, 2].imshow(pred, cmap="gray")
    axes[i, 2].set_title("prediction")
    for ax in axes[i]:
        ax.axis("off")
plt.tight_layout()
plt.show()"""),
    md("## BLOCK 14 — Save fine-tuned SegFormer"),
    code("""save_dir = Path("/content/export/segformer")
save_dir.mkdir(parents=True, exist_ok=True)
trainer.save_model(str(save_dir))
processor.save_pretrained(str(save_dir))
print("Saved to", save_dir)
print(list(save_dir.iterdir()))"""),
    md("## BLOCK 15 — Download / export model from Colab"),
    code("""import shutil
from google.colab import files

archive = "/content/export/segformer_cracks_model"
shutil.make_archive(archive, "zip", save_dir)
files.download(archive + ".zip")"""),
    md("## BLOCK 16 — Upload SegFormer model to Amazon S3"),
    code("""import os
import boto3
from google.colab import userdata

os.environ["AWS_ACCESS_KEY_ID"] = userdata.get("AWS_ACCESS_KEY_ID")
os.environ["AWS_SECRET_ACCESS_KEY"] = userdata.get("AWS_SECRET_ACCESS_KEY")
os.environ["AWS_DEFAULT_REGION"] = userdata.get("AWS_DEFAULT_REGION")
S3_BUCKET = userdata.get("S3_BUCKET")
s3 = boto3.client("s3", region_name=os.environ["AWS_DEFAULT_REGION"])
prefix = "models/segformer/"
for p in Path(save_dir).rglob("*"):
    if p.is_file():
        key = prefix + p.relative_to(save_dir).as_posix()
        s3.upload_file(str(p), S3_BUCKET, key)
        print("uploaded", key)
listed = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
for obj in listed.get("Contents", []):
    print("verify", obj["Key"], obj["Size"])"""),
]

PART_C = [
    md("""# PART C — Before / After Repair Verification (OpenCV + SSIM)

No neural fine-tuning. Matching filenames:

```text
before/road_001_before.jpg  ↔  after/road_001_after.jpg
```

Thresholds are **configurable project settings**, not claimed scientific constants."""),
    md("## BLOCK 1 — Install packages"),
    code("""%pip install -q opencv-python-headless scikit-image matplotlib Pillow numpy"""),
    md("## BLOCK 2 — Imports + configurable thresholds"),
    code("""from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
from skimage.metrics import structural_similarity as ssim


@dataclass
class RepairVerifyConfig:
    # Feature matching
    feature_backend: str = "ORB"  # "ORB" or "SIFT"
    min_match_count: int = 12
    ratio_test: float = 0.75
    ransac_reproj_threshold: float = 5.0
    # Change detection
    absdiff_blur_ksize: int = 5
    binary_threshold: int = 30  # 0-255 on absdiff
    min_change_area_px: int = 50
    # Decision thresholds (tune on your own labeled pairs later)
    completion_pass: float = 70.0
    deviation_flag: float = 25.0
    ssim_warn: float = 0.35


CFG = RepairVerifyConfig()
print(CFG)"""),
    md("## BLOCK 3 — Load before/after images"),
    code("""from google.colab import drive

drive.mount("/content/drive")

# --- EDIT ---
BA_ROOT = Path("/content/drive/MyDrive/datasets/before_after_dataset")
BEFORE_DIR = BA_ROOT / "before"
AFTER_DIR = BA_ROOT / "after"

print(BEFORE_DIR.exists(), AFTER_DIR.exists())
print("before count", len(list(BEFORE_DIR.glob("*"))) if BEFORE_DIR.exists() else 0)
print("after count", len(list(AFTER_DIR.glob("*"))) if AFTER_DIR.exists() else 0)"""),
    md("## BLOCK 4 — Validate matching pairs"),
    code("""IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def pair_key(path: Path) -> str:
    name = path.stem.lower()
    for suffix in ("_before", "-before", "_after", "-after"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def collect_pairs(before_dir: Path, after_dir: Path):
    befores = {pair_key(p): p for p in before_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS}
    afters = {pair_key(p): p for p in after_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS}
    keys = sorted(set(befores) & set(afters))
    missing_after = sorted(set(befores) - set(afters))
    missing_before = sorted(set(afters) - set(befores))
    pairs = [(k, befores[k], afters[k]) for k in keys]
    return pairs, missing_before, missing_after

pairs, missing_before, missing_after = collect_pairs(BEFORE_DIR, AFTER_DIR)
print("matched pairs:", len(pairs))
print("after without before:", missing_before[:10], "count", len(missing_before))
print("before without after:", missing_after[:10], "count", len(missing_after))
assert pairs, "No filename-matched pairs. Check *_before / *_after naming." """),
    md("## BLOCK 5 — Visualize pairs"),
    code("""fig, axes = plt.subplots(min(3, len(pairs)), 2, figsize=(10, 4 * min(3, len(pairs))))
if min(3, len(pairs)) == 1:
    axes = np.array([axes])
for axrow, (key, b, a) in zip(axes, pairs[:3]):
    axrow[0].imshow(cv2.cvtColor(cv2.imread(str(b)), cv2.COLOR_BGR2RGB))
    axrow[0].set_title(f"before {key}")
    axrow[0].axis("off")
    axrow[1].imshow(cv2.cvtColor(cv2.imread(str(a)), cv2.COLOR_BGR2RGB))
    axrow[1].set_title(f"after {key}")
    axrow[1].axis("off")
plt.tight_layout()
plt.show()"""),
    md("## BLOCK 6 — Feature detection"),
    code("""def make_detector(cfg: RepairVerifyConfig):
    if cfg.feature_backend.upper() == "SIFT":
        if not hasattr(cv2, "SIFT_create"):
            raise RuntimeError("SIFT unavailable in this OpenCV build; set feature_backend='ORB'")
        return cv2.SIFT_create()
    return cv2.ORB_create(nfeatures=4000)


def detect(img_bgr, cfg: RepairVerifyConfig):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    det = make_detector(cfg)
    kps, desc = det.detectAndCompute(gray, None)
    return gray, kps, desc

demo_key, demo_b, demo_a = pairs[0]
img_b = cv2.imread(str(demo_b))
img_a = cv2.imread(str(demo_a))
gray_b, kps_b, desc_b = detect(img_b, CFG)
gray_a, kps_a, desc_a = detect(img_a, CFG)
print("keypoints before/after:", len(kps_b), len(kps_a), "desc", None if desc_b is None else desc_b.shape)"""),
    md("## BLOCK 7 — Feature matching"),
    code("""def match_features(desc_b, desc_a, cfg: RepairVerifyConfig):
    if desc_b is None or desc_a is None:
        return []
    if cfg.feature_backend.upper() == "SIFT":
        index_params = dict(algorithm=1, trees=5)
        search_params = dict(checks=50)
        flann = cv2.FlannBasedMatcher(index_params, search_params)
        raw = flann.knnMatch(desc_b, desc_a, k=2)
    else:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        raw = bf.knnMatch(desc_b, desc_a, k=2)
    good = []
    for pair in raw:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < cfg.ratio_test * n.distance:
            good.append(m)
    return good

good = match_features(desc_b, desc_a, CFG)
print("good matches:", len(good))
match_vis = cv2.drawMatches(img_b, kps_b, img_a, kps_a, good[:80], None, flags=2)
plt.figure(figsize=(14, 6))
plt.imshow(cv2.cvtColor(match_vis, cv2.COLOR_BGR2RGB))
plt.axis("off")
plt.title("feature matches")
plt.show()"""),
    md("## BLOCK 8 — Homography"),
    code("""def homography(kps_b, kps_a, matches, cfg: RepairVerifyConfig):
    if len(matches) < cfg.min_match_count:
        return None, None
    src = np.float32([kps_b[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kps_a[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, cfg.ransac_reproj_threshold)
    return H, mask

H, inlier_mask = homography(kps_b, kps_a, good, CFG)
print("H:\\n", H)
print("inliers:", None if inlier_mask is None else int(inlier_mask.sum()))"""),
    md("## BLOCK 9 — Image alignment"),
    code("""def align_before_to_after(img_b, img_a, H):
    h, w = img_a.shape[:2]
    if H is None:
        resized = cv2.resize(img_b, (w, h))
        return resized, False
    warped = cv2.warpPerspective(img_b, H, (w, h))
    return warped, True

warped_b, aligned_ok = align_before_to_after(img_b, img_a, H)
print("aligned with homography:" , aligned_ok)
fig, ax = plt.subplots(1, 3, figsize=(15, 4))
ax[0].imshow(cv2.cvtColor(warped_b, cv2.COLOR_BGR2RGB)); ax[0].set_title("aligned before"); ax[0].axis("off")
ax[1].imshow(cv2.cvtColor(img_a, cv2.COLOR_BGR2RGB)); ax[1].set_title("after"); ax[1].axis("off")
blend = cv2.addWeighted(warped_b, 0.5, img_a, 0.5, 0)
ax[2].imshow(cv2.cvtColor(blend, cv2.COLOR_BGR2RGB)); ax[2].set_title("blend"); ax[2].axis("off")
plt.show()"""),
    md("## BLOCK 10 — SSIM"),
    code("""def compute_ssim(img1, img2):
    g1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    score, full = ssim(g1, g2, full=True)
    return float(score), full

ssim_score, ssim_map = compute_ssim(warped_b, img_a)
print("SSIM:", ssim_score)
plt.imshow(ssim_map, cmap="gray")
plt.title("SSIM map")
plt.axis("off")
plt.colorbar()
plt.show()"""),
    md("## BLOCK 11 — Change detection"),
    code("""def change_mask(img1, img2, cfg: RepairVerifyConfig):
    g1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    k = cfg.absdiff_blur_ksize | 1
    g1 = cv2.GaussianBlur(g1, (k, k), 0)
    g2 = cv2.GaussianBlur(g2, (k, k), 0)
    diff = cv2.absdiff(g1, g2)
    _, binary = cv2.threshold(diff, cfg.binary_threshold, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    return diff, binary

diff, binary = change_mask(warped_b, img_a, CFG)
plt.figure(figsize=(10, 4))
plt.subplot(1, 2, 1); plt.imshow(diff, cmap="gray"); plt.title("absdiff"); plt.axis("off")
plt.subplot(1, 2, 2); plt.imshow(binary, cmap="gray"); plt.title("change mask"); plt.axis("off")
plt.show()"""),
    md("## BLOCK 12 — Damaged-area reduction / completion percentage"),
    code("""def area_ratio(mask: np.ndarray) -> float:
    return float((mask > 0).mean() * 100.0)


def completion_from_masks(before_change_ratio: float, after_change_ratio: float) -> float:
    # Interprets remaining change vs original change. Configurable, not a validated civil-engineering metric.
    if before_change_ratio <= 1e-6:
        return 100.0 if after_change_ratio <= 1e-6 else 0.0
    reduction = (before_change_ratio - after_change_ratio) / before_change_ratio * 100.0
    return float(np.clip(reduction, 0.0, 100.0))

# Damage proxy: dark/defective pixels on grayscale (simple, dataset-dependent).
# Primary repair signal used below is change-mask remaining vs a reference empty mask.

before_ratio = area_ratio(change_mask(warped_b, warped_b, CFG)[1])  # ~0
# Remaining visual difference after repair:
after_change_ratio = area_ratio(binary)
# Use inverted SSIM as a second signal of remaining difference
remaining = after_change_ratio
completion_percentage = float(np.clip((ssim_score * 50.0) + (100.0 - remaining) / 2.0, 0, 100))
# More direct definition requested: damaged-area reduction using change pixels relative to before-only dark areas
before_dark = (cv2.cvtColor(warped_b, cv2.COLOR_BGR2GRAY) < 80).mean() * 100
after_dark = (cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY) < 80).mean() * 100
completion_percentage = completion_from_masks(before_dark if before_dark > 0 else remaining + 1e-3, after_dark if before_dark > 0 else remaining)
deviation_percentage = float(np.clip(100.0 - completion_percentage, 0, 100))
print({
    "ssim": ssim_score,
    "change_area_pct": after_change_ratio,
    "completion_percentage": completion_percentage,
    "deviation_percentage": deviation_percentage,
})"""),
    md("## BLOCK 13 — Verification result"),
    code("""def verify_pair(before_path: Path, after_path: Path, cfg: RepairVerifyConfig) -> dict:
    img_b = cv2.imread(str(before_path))
    img_a = cv2.imread(str(after_path))
    if img_b is None or img_a is None:
        raise FileNotFoundError((before_path, after_path))
    _, kps_b, desc_b = detect(img_b, cfg)
    _, kps_a, desc_a = detect(img_a, cfg)
    matches = match_features(desc_b, desc_a, cfg)
    H, _ = homography(kps_b, kps_a, matches, cfg)
    warped, aligned = align_before_to_after(img_b, img_a, H)
    score, _ = compute_ssim(warped, img_a)
    _, binary = change_mask(warped, img_a, cfg)
    before_dark = (cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) < 80).mean() * 100
    after_dark = (cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY) < 80).mean() * 100
    completion = completion_from_masks(max(before_dark, 1e-3), after_dark)
    deviation = float(np.clip(100.0 - completion, 0, 100))
    deviation_detected = bool(deviation >= cfg.deviation_flag or score < cfg.ssim_warn)
    if completion >= cfg.completion_pass and not deviation_detected:
        status = "verified"
    elif completion >= cfg.completion_pass * 0.5:
        status = "needs_review"
    else:
        status = "not_verified"
    return {
        "before": str(before_path),
        "after": str(after_path),
        "aligned": aligned,
        "match_count": len(matches),
        "ssim": score,
        "completion_percentage": completion,
        "deviation_percentage": deviation,
        "deviation_detected": deviation_detected,
        "verification_status": status,
        "change_area_percent": area_ratio(binary),
    }

results = [verify_pair(b, a, CFG) for _, b, a in pairs[:20]]
for r in results:
    print({k: r[k] for k in ("verification_status", "completion_percentage", "deviation_percentage", "ssim", "deviation_detected")})"""),
]

PART_D = [
    md("""# PART D — Colab test UI + Amazon S3 + Flask wiring

Use this **after** Part A / B (and C if you have before/after photos).

1. Test inference **inside Colab** with an upload form (Gradio).
2. Optionally push/pull weights on S3.
3. Copy the Flask snippets into `municipal_ai_system` (already added in this repo).

Flask does **not** retrain. Training stays in Colab."""),
    md("## BLOCK 1 — Install demo + AWS packages"),
    code("""%pip install -q gradio boto3 ultralytics transformers opencv-python-headless scikit-image Pillow timm"""),
    md("## BLOCK 2 — GPU check + paths to trained weights"),
    code("""import os
from pathlib import Path
import torch

print("CUDA:", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu")

YOLO_WEIGHTS = Path("/content/export/yolov8/best.pt")
SEGFORMER_DIR = Path("/content/export/segformer")

# Fallback: Ultralytics default run folder
if not YOLO_WEIGHTS.exists():
    found = list(Path("/content").rglob("best.pt"))
    YOLO_WEIGHTS = found[0] if found else YOLO_WEIGHTS
print("YOLO:", YOLO_WEIGHTS, YOLO_WEIGHTS.exists())
print("SegFormer:", SEGFORMER_DIR, SEGFORMER_DIR.exists())"""),
    md("## BLOCK 3 — Secure AWS credentials (never hard-code keys)"),
    code("""# Colab Secrets (key icon) must define:
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION, S3_BUCKET
from google.colab import userdata

for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
    os.environ[name] = userdata.get(name)
S3_BUCKET = userdata.get("S3_BUCKET")
print("region:", os.environ["AWS_DEFAULT_REGION"], "bucket set:", bool(S3_BUCKET))"""),
    md("## BLOCK 4 — boto3 client"),
    code("""import boto3

s3 = boto3.client("s3", region_name=os.environ["AWS_DEFAULT_REGION"])
print("S3 client ready")"""),
    md("## BLOCK 5 — Upload models to S3 (if not already uploaded in A/B)"),
    code("""def upload_file(local: Path, key: str):
    s3.upload_file(str(local), S3_BUCKET, key)
    print("uploaded", f"s3://{S3_BUCKET}/{key}")

if YOLO_WEIGHTS.exists():
    upload_file(YOLO_WEIGHTS, "models/yolov8/best.pt")
if SEGFORMER_DIR.exists():
    for p in SEGFORMER_DIR.rglob("*"):
        if p.is_file():
            upload_file(p, "models/segformer/" + p.relative_to(SEGFORMER_DIR).as_posix())"""),
    md("## BLOCK 6 — Verify S3 objects"),
    code("""for prefix in ("models/yolov8/", "models/segformer/"):
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    print("===", prefix, "===")
    for obj in resp.get("Contents", []):
        print(obj["Key"], obj["Size"])"""),
    md("## BLOCK 7 — Download models from S3 when Colab lost local files"),
    code("""local_yolo = Path("/content/models/yolov8/best.pt")
local_seg = Path("/content/models/segformer")
local_yolo.parent.mkdir(parents=True, exist_ok=True)
local_seg.mkdir(parents=True, exist_ok=True)

s3.download_file(S3_BUCKET, "models/yolov8/best.pt", str(local_yolo))
resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="models/segformer/")
for obj in resp.get("Contents", []):
    rel = obj["Key"].split("models/segformer/", 1)[-1]
    dest = local_seg / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(S3_BUCKET, obj["Key"], str(dest))
    print("downloaded", dest)

YOLO_WEIGHTS = local_yolo if local_yolo.exists() else YOLO_WEIGHTS
SEGFORMER_DIR = local_seg if any(local_seg.iterdir()) else SEGFORMER_DIR"""),
    md("## BLOCK 8 — Load inference models in Colab"),
    code("""from ultralytics import YOLO
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor
from PIL import Image
import numpy as np
import cv2

yolo = YOLO(str(YOLO_WEIGHTS)) if YOLO_WEIGHTS.exists() else None
seg_processor = SegformerImageProcessor.from_pretrained(str(SEGFORMER_DIR)) if SEGFORMER_DIR.exists() else None
seg_model = SegformerForSemanticSegmentation.from_pretrained(str(SEGFORMER_DIR)) if SEGFORMER_DIR.exists() else None
if seg_model is not None:
    seg_model.eval()
    if torch.cuda.is_available():
        seg_model.to("cuda")
print("YOLO loaded:", yolo is not None, "SegFormer loaded:", seg_model is not None)

ID_TO_NAME = {
    0: "D00_longitudinal_crack",
    1: "D10_transverse_crack",
    2: "D20_alligator_crack",
    3: "D40_pothole",
}"""),
    md("## BLOCK 9 — Inference helpers (YOLO + SegFormer + optional MiDaS)"),
    code("""import torch.nn.functional as F


def run_yolo(image: Image.Image, conf=0.25):
    if yolo is None:
        raise RuntimeError("YOLOv8 weights not found. Run Part A or download from S3.")
    arr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    res = yolo.predict(arr, conf=conf, device=0 if torch.cuda.is_available() else "cpu", verbose=False)[0]
    plotted = res.plot()
    dets = []
    if res.boxes is not None:
        for box, cls, score in zip(res.boxes.xyxy, res.boxes.cls, res.boxes.conf):
            dets.append({
                "class_id": int(cls),
                "class_name": ID_TO_NAME.get(int(cls), str(int(cls))),
                "confidence": float(score),
                "xyxy": [float(x) for x in box],
            })
    return Image.fromarray(cv2.cvtColor(plotted, cv2.COLOR_BGR2RGB)), dets


def run_segformer(image: Image.Image):
    if seg_model is None:
        raise RuntimeError("SegFormer folder not found. Run Part B or download from S3.")
    rgb = image.convert("RGB")
    inputs = seg_processor(images=rgb, return_tensors="pt")
    device = next(seg_model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        logits = seg_model(**inputs).logits
    up = F.interpolate(logits, size=rgb.size[::-1], mode="bilinear", align_corners=False)
    pred = up.argmax(dim=1)[0].cpu().numpy().astype(np.uint8)
    overlay = np.array(rgb)
    color = np.zeros_like(overlay)
    color[pred == 1] = (255, 0, 0)
    vis = cv2.addWeighted(overlay, 0.65, color, 0.35, 0)
    crack_pct = float((pred == 1).mean() * 100)
    return Image.fromarray(vis), Image.fromarray((pred * 255).astype(np.uint8)), crack_pct


# Optional relative depth — pretrained MiDaS, not fine-tuned here
midas = None
midas_transform = None

def load_midas():
    global midas, midas_transform
    midas = torch.hub.load("intel-isl/MiDaS", "MiDaS_small")
    midas.eval()
    if torch.cuda.is_available():
        midas.to("cuda")
    transforms = torch.hub.load("intel-isl/MiDaS", "transforms")
    midas_transform = transforms.small_transform


def run_midas(image: Image.Image):
    if midas is None:
        load_midas()
    img = np.array(image.convert("RGB"))
    inp = midas_transform(img).to(next(midas.parameters()).device)
    with torch.no_grad():
        pred = midas(inp)
        pred = torch.nn.functional.interpolate(
            pred.unsqueeze(1),
            size=img.shape[:2],
            mode="bicubic",
            align_corners=False,
        ).squeeze().cpu().numpy()
    pred = (pred - pred.min()) / (pred.max() - pred.min() + 1e-8)
    heat = (plt.cm.inferno(pred)[..., :3] * 255).astype(np.uint8)
    return Image.fromarray(heat), pred


def naive_severity(dets, crack_pct):
    # Configurable placeholder — not a published pavement index.
    potholes = [d for d in dets if d["class_id"] == 3]
    if any(d["confidence"] > 0.5 for d in potholes) or crack_pct > 8:
        level = "high"
    elif dets or crack_pct > 2:
        level = "medium"
    else:
        level = "low"
    return {"severity": level, "n_detections": len(dets), "n_potholes": len(potholes), "crack_pct": crack_pct}"""),
    md("## BLOCK 10 — Gradio interface: upload an image and inspect outputs"),
    code("""import gradio as gr
from repair_colab import verify_images  # defined in next cell if missing
"""),
]

# Part D block 10 got a bad import. I'll fix PART_D to be self-contained without repair_colab.

PART_D = PART_D[:-1] + [
    md("## BLOCK 10 — Gradio interface (upload image / before-after pair)"),
    code("""import gradio as gr
from skimage.metrics import structural_similarity as ssim_metric


def _verify_numpy(before_img, after_img):
    if before_img is None or after_img is None:
        return None, {"error": "Upload both before and after images."}
    b = cv2.cvtColor(np.array(before_img.convert("RGB")), cv2.COLOR_RGB2BGR)
    a = cv2.cvtColor(np.array(after_img.convert("RGB")), cv2.COLOR_RGB2BGR)
    gray_b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    gray_a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(4000)
    k1, d1 = orb.detectAndCompute(gray_b, None)
    k2, d2 = orb.detectAndCompute(gray_a, None)
    aligned = False
    warped = cv2.resize(b, (a.shape[1], a.shape[0]))
    if d1 is not None and d2 is not None:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, False)
        raw = bf.knnMatch(d1, d2, k=2)
        good = [m for m, n in raw if len((m, n)) == 2 and m.distance < 0.75 * n.distance] if raw else []
        # knnMatch robustness
        good = []
        for pair in raw:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good.append(m)
        if len(good) >= 12:
            src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
            if H is not None:
                warped = cv2.warpPerspective(b, H, (a.shape[1], a.shape[0]))
                aligned = True
    g1 = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    score, _ = ssim_metric(g1, g2, full=True)
    diff = cv2.absdiff(g1, g2)
    _, binary = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    before_dark = (g1 < 80).mean() * 100
    after_dark = (g2 < 80).mean() * 100
    if before_dark <= 1e-6:
        completion = 100.0 if after_dark <= 1e-6 else 0.0
    else:
        completion = float(np.clip((before_dark - after_dark) / before_dark * 100.0, 0, 100))
    deviation = float(np.clip(100.0 - completion, 0, 100))
    deviation_detected = deviation >= 25.0 or score < 0.35
    status = "verified" if completion >= 70 and not deviation_detected else ("needs_review" if completion >= 35 else "not_verified")
    overlay = a.copy()
    overlay[binary > 0] = (0, 0, 255)
    vis = cv2.addWeighted(a, 0.6, overlay, 0.4, 0)
    return Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)), {
        "aligned": aligned,
        "ssim": float(score),
        "completion_percentage": completion,
        "deviation_percentage": deviation,
        "deviation_detected": deviation_detected,
        "verification_status": status,
    }


def inspect_road(image, conf, use_depth):
    if image is None:
        return None, None, None, {"error": "Upload a road image."}
    yolo_vis, dets = run_yolo(image, conf=float(conf))
    seg_vis, mask, crack_pct = run_segformer(image)
    depth_vis = None
    depth_note = None
    if use_depth:
        depth_vis, _ = run_midas(image)
        depth_note = "MiDaS relative depth only (not metric)."
    summary = naive_severity(dets, crack_pct)
    summary["detections"] = dets
    summary["depth"] = depth_note
    return yolo_vis, seg_vis, depth_vis, summary


with gr.Blocks(title="Road Inspection — Colab test") as demo:
    gr.Markdown("# AI Road Inspection — Colab test UI\\nTrain in Parts A/B, then upload an image here before wiring Flask.")
    with gr.Tab("Detect + segment"):
        inp = gr.Image(type="pil", label="Road image")
        conf = gr.Slider(0.1, 0.9, value=0.25, label="YOLO confidence")
        use_depth = gr.Checkbox(label="Also run MiDaS relative depth", value=False)
        btn = gr.Button("Run pipeline")
        out_y = gr.Image(label="YOLOv8 detections")
        out_s = gr.Image(label="SegFormer crack overlay")
        out_d = gr.Image(label="MiDaS depth (optional)")
        out_j = gr.JSON(label="Structured result")
        btn.click(inspect_road, [inp, conf, use_depth], [out_y, out_s, out_d, out_j])
    with gr.Tab("Before / after verification"):
        b = gr.Image(type="pil", label="Before")
        a = gr.Image(type="pil", label="After")
        btn2 = gr.Button("Verify repair")
        out_ba = gr.Image(label="Change overlay")
        out_baj = gr.JSON(label="Verification")
        btn2.click(_verify_numpy, [b, a], [out_ba, out_baj])

demo.launch(share=True, debug=True)"""),
    md("""## BLOCK 11 — Flask-side S3 download (copy already lives in the repo)

The Flask app must only **load** weights. Example:

```python
import os, boto3
from pathlib import Path

def download_if_needed(bucket, key, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ["AWS_DEFAULT_REGION"],
    )
    s3.download_file(bucket, key, str(dest))
    return dest
```

Conceptual bucket layout:

```text
road-inspection/
├── models/yolov8/best.pt
├── models/segformer/   # config.json, model.safetensors, preprocessor_config.json, ...
├── uploads/before/
├── uploads/after/
└── reports/pdf|csv|json/
```

Training datasets do **not** need to be stored on S3.

Flask project target layout after integration:

```text
municipal_ai_system/
├── app/
│   ├── ai/
│   │   ├── yolo_infer.py
│   │   ├── segformer_infer.py
│   │   ├── repair_verify.py
│   │   └── pipeline.py
│   ├── services/s3_client.py
│   ├── templates/inspect.html
│   └── routes.py          # /inspect upload UI
└── weights/               # downloaded from S3, gitignored
    ├── yolov8/best.pt
    └── segformer/
```
"""),
]


def main() -> None:
    root = Path(__file__).resolve().parent
    write_nb(root / "part_a_rdd2022_yolov8.ipynb", PART_A)
    write_nb(root / "part_b_segformer_cracks.ipynb", PART_B)
    write_nb(root / "part_c_before_after.ipynb", PART_C)
    write_nb(root / "part_d_colab_ui_and_s3.ipynb", PART_D)


if __name__ == "__main__":
    main()
