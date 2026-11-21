"""Build a YOLO *segmentation* dataset for crack outlines (train later).

Purpose
-------
The main detector (D00/D10/D20/D40 boxes) finds cracks on the road photo. This
second model outlines the crack pixels *inside a detected crack box* (crop),
which gives crack length / width / area for severity and cost. Crops of a road
photo look like the close-up pavement images used here, so the domain matches.

Sources kept in ``data/Crack dataset`` (road pavement only): CRACK500, GAPS384,
CrackTree200, CFD, CrackForest, AEL ("Sylvie"). Wall/concrete sources were removed.

What it does
------------
* thresholds the (JPEG-noisy) masks, removes speckle, traces each crack as a
  polygon -> YOLO-seg label ``0 x1 y1 x2 y2 ...`` (class 0 = crack)
* de-duplicates images by hash (CFD / CrackForest overlap) and keeps test
  images out of train
* adds crack-free *Indian* road patches (448 px crops from RDD India images
  with no damage label) as negatives, so plain asphalt is not outlined

Example
-------
    python scripts/training/build_crack_seg.py --out data/build/crack_seg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DEFAULT_BUILD, REPO_ROOT  # noqa: E402

DEFAULT_SRC = REPO_ROOT / "data" / "Crack dataset"
DEFAULT_NEG_SRC = REPO_ROOT / "data" / "road_v2" / "train"
ROAD_SOURCES = ("CRACK500", "GAPS384", "cracktree200", "CFD", "forest", "Sylvie")
MIN_AREA_PX = 20


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def mask_to_polygons(mask_path: Path) -> list[str]:
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return []
    h, w = mask.shape
    _, bw = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lines = []
    for contour in contours:
        if cv2.contourArea(contour) < MIN_AREA_PX and cv2.arcLength(contour, False) < 15:
            continue
        poly = cv2.approxPolyDP(contour, 1.0, True).reshape(-1, 2)
        if len(poly) < 3:
            continue
        coords = " ".join(f"{x / w:.5f} {y / h:.5f}" for x, y in poly)
        lines.append(f"0 {coords}")
    return lines


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _negatives(neg_src: Path, count: int, out_dir: Path, seed: int) -> int:
    """448x448 crops of the road area from RDD India images with no damage."""
    if count <= 0 or not (neg_src / "labels").is_dir():
        return 0
    empties = [p for p in sorted((neg_src / "labels").glob("*.txt")) if not p.read_text().strip()]
    random.Random(seed).shuffle(empties)
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)
    made = 0
    for lbl in empties:
        if made >= count:
            break
        img = cv2.imread(str(neg_src / "images" / f"{lbl.stem}.jpg"))
        if img is None:
            continue
        h, w = img.shape[:2]
        # Dashcam frames: road occupies roughly the bottom third, above the bonnet.
        side = int(0.24 * h)
        y0 = h - side - int(0.14 * h)
        x0 = int(w * random.Random(seed + made).uniform(0.25, 0.75)) - side // 2
        x0 = max(0, min(w - side, x0))
        if side < 64 or y0 < h // 2:
            continue
        crop = cv2.resize(img[y0:y0 + side, x0:x0 + side], (448, 448), interpolation=cv2.INTER_AREA)
        name = f"negroad_{lbl.stem}"
        cv2.imwrite(str(out_dir / "images" / f"{name}.jpg"), crop)
        (out_dir / "labels" / f"{name}.txt").write_text("", encoding="utf-8")
        made += 1
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_BUILD / "crack_seg")
    ap.add_argument("--negatives", type=int, default=500, help="crack-free Indian road crops for train")
    ap.add_argument("--neg-src", type=Path, default=DEFAULT_NEG_SRC)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    stats = Counter()
    seen: dict[str, str] = {}
    plan = {"train": [], "val": []}
    for src_split, out_split in (("test", "val"), ("train", "train")):  # val first: it wins dedupe
        img_dir, mask_dir = args.src / src_split / "images", args.src / src_split / "masks"
        for img in sorted(img_dir.glob("*.jpg")):
            if not img.name.startswith(ROAD_SOURCES):
                stats["skipped_non_road_source"] += 1
                continue
            digest = _md5(img)
            if digest in seen:
                stats[f"duplicate_removed_{out_split}"] += 1
                continue
            seen[digest] = out_split
            mask = mask_dir / img.name
            if not mask.exists():
                stats["missing_mask"] += 1
                continue
            plan[out_split].append((img, mask))

    report = {
        "train_images": len(plan["train"]),
        "val_images": len(plan["val"]),
        "by_source_train": dict(Counter(p[0].name.split("_")[0] for p in plan["train"])),
        "negatives_requested": args.negatives,
        "cleaning": dict(stats),
    }
    print(json.dumps(report, indent=2))
    if args.dry_run:
        return 0

    polys = Counter()
    for split, items in plan.items():
        img_out, lbl_out = args.out / split / "images", args.out / split / "labels"
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)
        for img, mask in items:
            lines = mask_to_polygons(mask)
            polys[split] += len(lines)
            if not lines:
                polys[f"{split}_empty_masks"] += 1
            _link_or_copy(img, img_out / img.name)
            (lbl_out / f"{img.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""),
                                                      encoding="utf-8")
    report["negatives_added"] = _negatives(args.neg_src, args.negatives, args.out / "train", args.seed)
    report["polygons"] = dict(polys)

    import yaml

    (args.out / "data.yaml").write_text(
        yaml.safe_dump({"path": args.out.resolve().as_posix(), "train": "train/images",
                        "val": "val/images", "names": {0: "crack"}, "nc": 1}, sort_keys=False),
        encoding="utf-8")
    (args.out / "build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("negatives_added", "polygons")}, indent=2))
    print(f"Wrote {args.out / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
