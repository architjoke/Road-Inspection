"""Convert official RDD2022 India Pascal VOC labels to YOLO (data/road_v2).

Why: the YOLO copy in ``data/road`` (from RDD_SPLIT) numbered classes wrongly —
its class 3 marks faded lane paint / crosswalks instead of potholes. The
official XML stores class *names*, so the mapping here is by name:

    D00 -> 0 longitudinal crack     D10 -> 1 transverse crack
    D20 -> 2 alligator crack        D40 -> 3 pothole
    anything else (D43 crosswalk blur, D44 white-line blur, D50 manhole,
    D01/D11/D0w0 ...) is dropped and counted.

Images keep the train/val/test membership they had in the old ``data/road`` split, so
results stay comparable; images not present there go to train.

Example
-------
    python -I scripts/training/convert_rdd_xml.py --src data/rdd2022_india/India/train \
        --split-ref data/road_v2 --out data/road_v2
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CLASS_NAMES  # noqa: E402

NAME_TO_ID = {"D00": 0, "D10": 1, "D20": 2, "D40": 3}
SPLITS = ("train", "val", "test")


def parse_voc(xml_path: Path) -> tuple[int, int, list[tuple[str, float, float, float, float]]]:
    raw = xml_path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:  # untrusted input: no DTDs/entities
        raise ValueError(f"refusing XML with DTD/entities: {xml_path.name}")
    root = ET.fromstring(raw)
    size = root.find("size")
    width = int(float(size.findtext("width"))) if size is not None else 0
    height = int(float(size.findtext("height"))) if size is not None else 0
    objects = []
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip()
        box = obj.find("bndbox")
        if box is None:
            continue
        coords = [float(box.findtext(k)) for k in ("xmin", "ymin", "xmax", "ymax")]
        objects.append((name, *coords))
    return width, height, objects


def link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path("data/rdd2022_india/India/train"))
    ap.add_argument("--split-ref", type=Path, default=Path("data/road_v2"),
                    help="existing split to copy train/val/test membership from")
    ap.add_argument("--out", type=Path, default=Path("data/road_v2"))
    args = ap.parse_args(argv)

    xml_dir = args.src / "annotations" / "xmls"
    img_dir = args.src / "images"
    if not xml_dir.is_dir() or not img_dir.is_dir():
        print(f"ERROR: expected {xml_dir} and {img_dir}")
        return 2

    membership = {}
    for split in SPLITS:
        folder = args.split_ref / split / "images"
        if folder.is_dir():
            for p in folder.iterdir():
                membership[p.stem] = split

    kept, dropped = Counter(), Counter()
    per_split = {s: Counter() for s in SPLITS}
    images_per_split = Counter()
    from PIL import Image

    for xml_path in sorted(xml_dir.glob("*.xml")):
        stem = xml_path.stem
        img = next((img_dir / f"{stem}{ext}" for ext in (".jpg", ".jpeg", ".png")
                    if (img_dir / f"{stem}{ext}").exists()), None)
        if img is None:
            dropped["missing_image"] += 1
            continue
        width, height, objects = parse_voc(xml_path)
        if not width or not height:
            with Image.open(img) as im:
                width, height = im.size
        lines = []
        for name, x1, y1, x2, y2 in objects:
            cid = NAME_TO_ID.get(name)
            if cid is None:
                dropped[name] += 1
                continue
            x1, x2 = max(0.0, min(x1, x2)), min(float(width), max(x1, x2))
            y1, y2 = max(0.0, min(y1, y2)), min(float(height), max(y1, y2))
            if x2 - x1 < 2 or y2 - y1 < 2:
                dropped["degenerate_box"] += 1
                continue
            lines.append(
                f"{cid} {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
                f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
            )
            kept[name] += 1
        split = membership.get(stem, "train")
        out_img = args.out / split / "images"
        out_lbl = args.out / split / "labels"
        out_img.mkdir(parents=True, exist_ok=True)
        out_lbl.mkdir(parents=True, exist_ok=True)
        link_or_copy(img, out_img / img.name)
        (out_lbl / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        images_per_split[split] += 1
        for line in lines:
            per_split[split][CLASS_NAMES[int(line.split()[0])]] += 1

    import yaml

    (args.out / "data.yaml").write_text(
        yaml.safe_dump(
            {"path": args.out.resolve().as_posix(), "train": "train/images", "val": "val/images",
             "test": "test/images", "names": CLASS_NAMES, "nc": len(CLASS_NAMES)},
            sort_keys=False),
        encoding="utf-8")
    report = {
        "source": "RDD2022 India (official, CRDDC2022 figshare 10.6084/m9.figshare.21431547, CC BY-SA 4.0)",
        "images_per_split": dict(images_per_split),
        "boxes_kept_by_name": dict(kept),
        "boxes_dropped_by_name": dict(dropped),
        "boxes_per_split": {s: dict(c) for s, c in per_split.items()},
    }
    (args.out / "conversion_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
