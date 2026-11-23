"""Build the India road-damage training set (YOLO format).

Sources
-------
1. ``data/road_v2``           RDD2022 India (official XML converted by name) — the core.
2. ``data/india_extra/<name>`` optional extra INDIAN data in YOLO format, e.g. your
                              municipality's labelled phone photos or an Indian
                              pothole set exported from Roboflow/CVAT. Each folder
                              needs ``data.yaml`` (``names``) + ``train``/``valid``
                              (or ``val``)/``test`` sub-folders with images+labels.
                              Class names are mapped by name (pothole, alligator,
                              longitudinal, transverse); ``--class-map`` overrides.

What it does
------------
* validates + clamps every label line, drops non-target classes
* de-duplicates by image hash and removes any train image that also appears in
  val/test (leakage)
* caps background (no-damage) images in train to ``--bg-frac``
* oversamples rare classes by repeating image paths in ``train.txt``
  (Ultralytics keeps duplicates; mosaic/HSV/flip make each repeat different)
* extra-source val images form a separate ``local_val`` set (gate on both)

Validation is always the untouched RDD India val split, so runs stay comparable
with the current production model.

``--fold-test`` (final production model): the RDD India *test* split joins
train (~+23 % labelled damage). Val still picks the checkpoint and tunes the
thresholds, but there is no untouched test score any more — data.yaml then has
no ``test`` entry and evaluate.py reports val only.

Optional pretraining set (``--pretrain-src``)
--------------------------------------------
India has only ~2.2k damaged training images. ``--pretrain-src <RDD_SPLIT>``
also writes ``<out>_pretrain/`` with the *other* RDD2022 countries (Japan,
Czech, Norway, United States, China motorbike; China drone is top-down and is
excluded). Its val split is still **India val**, and an India fine-tune always
follows, so only Indian-road accuracy decides what ships. Skip it if
India-only training already passes the gate.

Example
-------
    python scripts/training/build_dataset.py --out data/build/india_v3
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CLASS_NAMES,
    DEFAULT_BUILD,
    DEFAULT_EXTRA,
    DEFAULT_ROAD,
    IMAGE_EXTS,
    SHORT,
    map_class_name,
)

SPLIT_ALIASES = {"train": ("train",), "val": ("val", "valid", "validation"), "test": ("test",)}
MIN_BOX_SIDE = 0.004  # normalized; smaller boxes are annotation noise


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_yaml_names(yaml_path: Path) -> dict[int, str]:
    import yaml

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    names = data.get("names") or {}
    if isinstance(names, list):
        return {i: str(n) for i, n in enumerate(names)}
    return {int(k): str(v) for k, v in names.items()}


def _clean_label(
    label_path: Path, id_map: dict[int, int | None], stats: Counter
) -> list[str]:
    if not label_path.exists():
        return []
    out = []
    for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 5:  # polygons / junk
            stats["dropped_malformed"] += 1
            continue
        try:
            src_cls = int(float(parts[0]))
            xc, yc, w, h = (float(v) for v in parts[1:])
        except ValueError:
            stats["dropped_malformed"] += 1
            continue
        cls = id_map.get(src_cls)
        if cls is None:
            stats["dropped_unmapped_class"] += 1
            continue
        # clamp box to the image
        x1, y1 = max(0.0, xc - w / 2), max(0.0, yc - h / 2)
        x2, y2 = min(1.0, xc + w / 2), min(1.0, yc + h / 2)
        w, h = x2 - x1, y2 - y1
        if w < MIN_BOX_SIDE or h < MIN_BOX_SIDE:
            stats["dropped_tiny_box"] += 1
            continue
        out.append(f"{cls} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {w:.6f} {h:.6f}")
    return out


def _images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)


def collect_source(
    root: Path, tag: str, id_map: dict[int, int | None], stats: Counter
) -> dict[str, list[dict]]:
    items: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    for split, aliases in SPLIT_ALIASES.items():
        for alias in aliases:
            img_dir = root / alias / "images"
            if not img_dir.is_dir():
                continue
            lbl_dir = root / alias / "labels"
            for img in _images(img_dir):
                lines = _clean_label(lbl_dir / f"{img.stem}.txt", id_map, stats)
                items[split].append(
                    {"src": img, "lines": lines, "source": tag, "hash": _md5(img)}
                )
            break
    return items


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _parse_kv(pairs: list[str], value_type=float) -> dict[str, float]:
    out = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        out[key.strip()] = value_type(value)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--road", type=Path, default=DEFAULT_ROAD, help="RDD India YOLO split")
    ap.add_argument("--extra", type=Path, default=DEFAULT_EXTRA, help="folder of extra Indian datasets")
    ap.add_argument("--out", type=Path, default=DEFAULT_BUILD / "india_v3")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--bg-frac", type=float, default=0.25,
                    help="max fraction of train images with no damage (RDD India is ~59%%)")
    ap.add_argument("--repeat", nargs="*", default=["D10=4", "D00=2", "D20=1", "D40=1"],
                    help="per-class image repeat factor, e.g. D10=4 D00=2")
    ap.add_argument("--extra-repeat", type=float, default=3.0,
                    help="repeat factor for extra (local Indian) train images")
    ap.add_argument("--class-map", nargs="*", default=[],
                    help="override name mapping for extra sets, e.g. crack=D00 'Pothole Severe'=D40")
    ap.add_argument("--pretrain-src", type=Path, default=None,
                    help="RDD_SPLIT folder (all countries) to build an optional pretraining set")
    ap.add_argument("--pretrain-exclude", nargs="*", default=["India_", "China_Drone"],
                    help="filename prefixes left out of the pretraining set")
    ap.add_argument("--fold-test", action="store_true",
                    help="train on the RDD India test split too (no held-out test score afterwards)")
    ap.add_argument("--dry-run", action="store_true", help="print stats only, write nothing")
    args = ap.parse_args(argv)

    random.seed(args.seed)
    stats: Counter = Counter()
    short_to_id = {v: k for k, v in SHORT.items()}
    repeat = {short_to_id[k]: v for k, v in _parse_kv(args.repeat).items() if k in short_to_id}
    overrides = {k.lower(): short_to_id[v] for k, v in _parse_kv(args.class_map, str).items()
                 if v in short_to_id}

    if not (args.road / "train" / "images").is_dir():
        print(f"ERROR: {args.road}/train/images not found (run scripts/training/fetch_rdd2022_india.py + convert_rdd_xml.py)")
        return 2

    # 1. Core: RDD India (ids already 0-3)
    road_map = {i: i for i in CLASS_NAMES}
    road_names = args.road / "data.yaml"
    if road_names.exists():
        names = _read_yaml_names(road_names)
        road_map = {i: map_class_name(n) for i, n in names.items()}
    print("Reading RDD India ...")
    splits = collect_source(args.road, "rdd_india", road_map, stats)

    # 2. Extra Indian datasets
    local_val: list[dict] = []
    extra_sources = []
    if args.extra.is_dir():
        for folder in sorted(p for p in args.extra.iterdir() if p.is_dir()):
            yaml_path = folder / "data.yaml"
            if not yaml_path.exists():
                print(f"  skip {folder.name}: no data.yaml")
                continue
            names = _read_yaml_names(yaml_path)
            id_map = {}
            for i, n in names.items():
                id_map[i] = overrides.get(n.lower(), map_class_name(n))
            mapped = {n: (SHORT[id_map[i]] if id_map[i] is not None else "DROP") for i, n in names.items()}
            print(f"  extra '{folder.name}': class mapping {mapped}")
            got = collect_source(folder, f"extra_{folder.name}", id_map, stats)
            splits["train"] += got["train"]
            local_val += got["val"] + got["test"]
            extra_sources.append(folder.name)

    if args.fold_test:
        stats["test_images_folded_into_train"] = len(splits["test"])
        splits["train"] += splits["test"]
        splits["test"] = []

    # 3. De-duplicate + leakage removal (val/test are authoritative)
    held_out = {it["hash"] for s in ("val", "test") for it in splits[s]}
    seen: set[str] = set()
    train = []
    for it in splits["train"]:
        if it["hash"] in held_out:
            stats["train_removed_leak"] += 1
            continue
        if it["hash"] in seen:
            stats["train_removed_duplicate"] += 1
            continue
        seen.add(it["hash"])
        train.append(it)
    local_val = [it for it in local_val if it["hash"] not in seen and it["hash"] not in held_out]

    # 4. Cap backgrounds
    positives = [it for it in train if it["lines"]]
    backgrounds = [it for it in train if not it["lines"]]
    max_bg = int(len(positives) * args.bg_frac / max(1e-6, 1 - args.bg_frac))
    random.shuffle(backgrounds)
    kept_bg = backgrounds[:max_bg]
    stats["train_backgrounds_dropped"] = len(backgrounds) - len(kept_bg)
    train = positives + kept_bg

    # 5. Oversampling plan
    def reps(it: dict) -> int:
        classes = {int(l.split()[0]) for l in it["lines"]}
        r = max([repeat.get(c, 1.0) for c in classes] or [1.0])
        if it["source"].startswith("extra_"):
            r *= args.extra_repeat
        return max(1, round(r))

    def box_counts(items, weighted=False):
        c = Counter()
        for it in items:
            w = reps(it) if weighted else 1
            for line in it["lines"]:
                c[SHORT[int(line.split()[0])]] += w
        return dict(sorted(c.items()))

    report = {
        "out": str(args.out),
        "sources": ["rdd_india"] + extra_sources,
        "train_images": len(train),
        "train_backgrounds": len(kept_bg),
        "train_list_entries": sum(reps(it) for it in train),
        "val_images": len(splits["val"]),
        "test_images": len(splits["test"]),
        "local_val_images": len(local_val),
        "boxes_train": box_counts(train),
        "boxes_train_effective": box_counts(train, weighted=True),
        "boxes_val": box_counts(splits["val"]),
        "boxes_local_val": box_counts(local_val),
        "repeat": {SHORT[k]: v for k, v in repeat.items()},
        "extra_repeat": args.extra_repeat,
        "bg_frac": args.bg_frac,
        "cleaning": dict(stats),
        "seed": args.seed,
        "fold_test": bool(args.fold_test),
    }
    print(json.dumps(report, indent=2))
    if args.dry_run:
        return 0

    # 6. Materialize (hard links when possible — no extra disk)
    out = args.out.resolve()
    groups = {"train": train, "val": splits["val"], "test": splits["test"], "local_val": local_val}
    for split, items in groups.items():
        if not items and split != "train":
            continue  # no empty split folders (Ultralytics rejects an empty test/val dir)
        img_dir, lbl_dir = out / split / "images", out / split / "labels"
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        for it in items:
            name = f"{it['source']}__{it['src'].name}"
            it["dst"] = img_dir / name
            _link_or_copy(it["src"], it["dst"])
            (lbl_dir / f"{Path(name).stem}.txt").write_text(
                "\n".join(it["lines"]) + ("\n" if it["lines"] else ""), encoding="utf-8"
            )

    lines = []
    for it in train:
        lines += [it["dst"].as_posix()] * reps(it)
    random.shuffle(lines)
    (out / "train.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    import yaml

    base = {"path": out.as_posix(), "names": CLASS_NAMES, "nc": len(CLASS_NAMES)}
    main_yaml = {**base, "train": "train.txt", "val": "val/images"}
    if splits["test"]:
        main_yaml["test"] = "test/images"
    (out / "data.yaml").write_text(yaml.safe_dump(main_yaml, sort_keys=False), encoding="utf-8")
    if local_val:
        (out / "local_val.yaml").write_text(
            yaml.safe_dump({**base, "train": "train.txt", "val": "local_val/images"},
                           sort_keys=False), encoding="utf-8")
    (out / "build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out / 'data.yaml'}")

    if args.pretrain_src:
        build_pretrain(args, out, stats)
    return 0


def build_pretrain(args, out: Path, stats: Counter) -> None:
    """Non-India RDD countries for stage-0 pretraining; validated on India val."""
    import yaml

    src = args.pretrain_src
    names_yaml = src / "data.yaml"
    id_map = {i: i for i in CLASS_NAMES}  # RDD_SPLIT convention: 0-3 = D00/D10/D20/D40
    if names_yaml.exists():
        id_map = {i: map_class_name(n) for i, n in _read_yaml_names(names_yaml).items()}
    else:
        print("WARNING: no data.yaml in pretrain source; assuming ids 0-3 = D00/D10/D20/D40")
    pre = Path(str(out) + "_pretrain")
    img_dir, lbl_dir = pre / "train" / "images", pre / "train" / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    countries: Counter = Counter()
    entries = []
    # India images are excluded, so every non-India split may be used for training.
    for split in ("train", "val", "test"):
        for img in _images(src / split / "images"):
            if any(img.name.startswith(p) for p in args.pretrain_exclude):
                continue
            lines = _clean_label(src / split / "labels" / f"{img.stem}.txt", id_map, stats)
            if not lines:
                continue  # backgrounds already come from the India data
            dst = img_dir / img.name
            _link_or_copy(img, dst)
            (lbl_dir / f"{img.stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            countries[img.name.rsplit("_", 1)[0]] += 1
            entries.append(dst.as_posix())
    (pre / "train.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
    (pre / "data.yaml").write_text(
        yaml.safe_dump(
            {
                "path": pre.as_posix(),
                "train": "train.txt",
                "val": (out / "val" / "images").as_posix(),
                "names": CLASS_NAMES,
                "nc": len(CLASS_NAMES),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    print(f"Pretraining set: {len(entries)} damaged images by country {dict(countries)}")
    print(f"Wrote {pre / 'data.yaml'} (val = India val)")


if __name__ == "__main__":
    raise SystemExit(main())
