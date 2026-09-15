"""Copy India-only RDD YOLO files into data/road/.

Keeps images whose names start with India_, and label lines for classes 0-3 only.

Examples:
  python scripts/filter_india_road.py
  python scripts/filter_india_road.py --src "G:\\My Drive\\RDD_SPLIT"
"""
from __future__ import annotations

import argparse
import shutil
from collections import Counter
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
KEEP_CLASSES = {0, 1, 2, 3}
COUNTRY_PREFIX = "India_"
SPLITS = ("train", "val", "test")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "data" / "road"

DEFAULT_SOURCES = [
    Path("/content/RDD_SPLIT"),
    Path("/content/drive/MyDrive/RDD_SPLIT"),
    Path("/content/drive/MyDrive/datasets/RDD_SPLIT"),
    Path("/content/drive/MyDrive/datasets/archive/RDD_SPLIT"),
    Path(r"G:\.shortcut-targets-by-id\16aHH0rPP5-0BSoC3LV_5yj0AT5r46Ed3\RDD_SPLIT"),
    Path(r"G:\My Drive\RDD_SPLIT"),
    Path(r"G:\My Drive\datasets\archive\RDD_SPLIT"),
    Path.home() / "Google Drive" / "My Drive" / "RDD_SPLIT",
    Path.home() / "My Drive" / "RDD_SPLIT",
    REPO_ROOT / "datasets" / "archive" / "RDD_SPLIT",
    REPO_ROOT / "RDD_SPLIT",
]


def find_repo_on_colab() -> Path | None:
    mydrive = Path("/content/drive/MyDrive")
    if not mydrive.exists():
        return None
    candidates = [
        mydrive / "Road Inspection System",
        mydrive / "Road-Inspection-System",
        mydrive / "RoadInspectionSystem",
        mydrive / "Desktop" / "Road Inspection System",
    ]
    for p in candidates:
        if (p / "municipal_ai_system" / "app" / "ai_models").exists():
            return p
    for child in mydrive.iterdir():
        if child.is_dir() and (child / "municipal_ai_system" / "app" / "ai_models").exists():
            return child
    return None


def resolve_src(explicit: Path | None) -> Path:
    if explicit is not None:
        src = explicit.expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src}")
        return src
    for candidate in DEFAULT_SOURCES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not find RDD_SPLIT. Pass it with --src.\n"
        "Expected layout: RDD_SPLIT/{train,val,test}/{images,labels}"
    )


def resolve_dest(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    colab_repo = find_repo_on_colab()
    if colab_repo is not None:
        return colab_repo / "data" / "road"
    return DEFAULT_DEST


def is_india(path: Path) -> bool:
    return path.name.startswith(COUNTRY_PREFIX) or path.name.lower().startswith("india_")


def filter_label_text(lbl: Path) -> tuple[str, Counter, Counter]:
    kept: Counter = Counter()
    dropped: Counter = Counter()
    lines: list[str] = []
    if not lbl.exists():
        return "", kept, dropped
    try:
        raw_text = lbl.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        print(f"  skip label (Drive read error): {lbl.name}: {exc}")
        return "", kept, dropped
    for raw in raw_text.splitlines():
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 5:
            continue
        cls = int(float(parts[0]))
        if cls not in KEEP_CLASSES:
            dropped[cls] += 1
            continue
        lines.append(" ".join(parts[:5]))
        kept[cls] += 1
    text = "\n".join(lines) + ("\n" if lines else "")
    return text, kept, dropped


def copy_if_needed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            return
        shutil.copy2(src, dst)
    except OSError as exc:
        print(f"  skip image (Drive copy error): {src.name}: {exc}")


def run(src: Path, dest: Path) -> None:
    print("Source:", src)
    print("Dest:  ", dest)
    stats = {
        split: {"images": 0, "boxes": Counter(), "dropped": Counter()}
        for split in SPLITS
    }
    for split in SPLITS:
        src_img = src / split / "images"
        src_lbl = src / split / "labels"
        dst_img = dest / split / "images"
        dst_lbl = dest / split / "labels"
        dst_img.mkdir(parents=True, exist_ok=True)
        dst_lbl.mkdir(parents=True, exist_ok=True)
        if not src_img.exists():
            print(f"missing {src_img}")
            continue
        files = [p for p in src_img.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        print(f"{split}: scanning {len(files)} images...")
        for i, img in enumerate(files, 1):
            if not is_india(img):
                continue
            text, kept, dropped = filter_label_text(src_lbl / f"{img.stem}.txt")
            copy_if_needed(img, dst_img / img.name)
            (dst_lbl / f"{img.stem}.txt").write_text(text, encoding="utf-8")
            stats[split]["images"] += 1
            stats[split]["boxes"].update(kept)
            stats[split]["dropped"].update(dropped)
            if i % 500 == 0:
                print(f"  {split} scanned {i}/{len(files)}, kept {stats[split]['images']}")

    names = {
        0: "D00_longitudinal_crack",
        1: "D10_transverse_crack",
        2: "D20_alligator_crack",
        3: "D40_pothole",
    }
    yaml_text = (
        f"path: {dest.as_posix()}\n"
        "train: train/images\n"
        "val: val/images\n"
        "test: test/images\n"
        "nc: 4\n"
        "names:\n"
        f"  0: {names[0]}\n"
        f"  1: {names[1]}\n"
        f"  2: {names[2]}\n"
        f"  3: {names[3]}\n"
    )
    (dest / "data.yaml").write_text(yaml_text, encoding="utf-8")

    for split, s in stats.items():
        print(f"\n=== {split} (India only) ===")
        print("images:", s["images"])
        print("boxes:", dict(s["boxes"]))
        print("dropped non-target classes:", dict(s["dropped"]))

    if stats["train"]["images"] == 0:
        sample = []
        train_img = src / "train" / "images"
        if train_img.exists():
            sample = [p.name for p in list(train_img.iterdir())[:8] if p.is_file()]
        raise SystemExit(
            f"No India images found with prefix '{COUNTRY_PREFIX}'. "
            f"Sample filenames: {sample}"
        )
    print("\nDone. Filtered road set is at", dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter India RDD files into data/road")
    parser.add_argument("--src", type=Path, default=None, help="Path to RDD_SPLIT")
    parser.add_argument("--dest", type=Path, default=None, help="Output folder (default: <repo>/data/road)")
    args = parser.parse_args()
    run(resolve_src(args.src), resolve_dest(args.dest))


if __name__ == "__main__":
    main()
