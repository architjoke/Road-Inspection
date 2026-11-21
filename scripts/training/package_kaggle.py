"""Package everything Kaggle needs into one zip (upload it as a Kaggle Dataset).

Contents of ``artifacts/kaggle/road-inspection-india.zip``:

    road_v2/{train,val,test}/{images,labels}   corrected RDD2022 India data
    scripts/training/*.py                       the training pipeline
    artifacts/ai_models/best.pt                 current production model (A/B baseline)

The ready-to-import notebook is copied next to it:
``artifacts/kaggle/kaggle_train.ipynb``.

Usage:
    python scripts/training/package_kaggle.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import APP_MODELS, DEFAULT_ROAD, REPO_ROOT  # noqa: E402

OUT_DIR = REPO_ROOT / "artifacts" / "kaggle"
NOTEBOOK = Path(__file__).resolve().parent / "kaggle_train.ipynb"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--road", type=Path, default=DEFAULT_ROAD)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "road-inspection-india.zip")
    args = ap.parse_args(argv)

    if not (args.road / "train" / "images").is_dir():
        print(f"ERROR: {args.road} missing — run fetch_rdd2022_india.py + convert_rdd_xml.py first")
        return 2
    baseline = APP_MODELS / "best.pt"
    args.out.parent.mkdir(parents=True, exist_ok=True)

    files = 0
    with zipfile.ZipFile(args.out, "w") as zf:
        for split in ("train", "val", "test"):
            for kind in ("images", "labels"):
                folder = args.road / split / kind
                for f in sorted(folder.iterdir()):
                    if f.suffix == ".cache":
                        continue
                    # JPEGs are already compressed: store them, deflate the label text
                    mode = zipfile.ZIP_STORED if kind == "images" else zipfile.ZIP_DEFLATED
                    zf.write(f, f"road_v2/{split}/{kind}/{f.name}", compress_type=mode)
                    files += 1
        for f in sorted(Path(__file__).resolve().parent.glob("*.py")):
            zf.write(f, f"scripts/training/{f.name}", compress_type=zipfile.ZIP_DEFLATED)
            files += 1
        if baseline.is_file():
            zf.write(baseline, "artifacts/ai_models/best.pt", compress_type=zipfile.ZIP_STORED)
            files += 1
        else:
            print("WARNING: no artifacts/ai_models/best.pt — the Kaggle run will skip the A/B gate")

    shutil.copy2(NOTEBOOK, args.out.parent / NOTEBOOK.name)
    size_mb = args.out.stat().st_size / 1e6
    print(f"Wrote {args.out} ({files} files, {size_mb:.0f} MB)")
    print(f"Notebook: {args.out.parent / NOTEBOOK.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
