"""Train the India road-damage detector with the locked recipe.

Profiles
--------
laptop    yolo11s @ 768, 150 epochs, batch 16   (RTX 4060 8 GB, ~6-9 h without AMP)
best      yolo11m @ 768, 150 epochs, batch 8    (~15-20 h without AMP on a 4060)
pretrain  stage 0 on the non-India RDD set (60 epochs), judged on India val
finetune  continue from --weights on the India set, low LR (60 epochs)
crack_seg yolo11s-seg @ 448 on data/build/crack_seg (crack outlines inside
          detected crack boxes; build it with scripts/training/build_crack_seg.py)

Why 768: RDD India images are 720x720, so larger sizes only upsample. Inference
uses the same size automatically (written to the model card on promotion).

AMP stays OFF by default — on this 4060 setup AMP produced NaN losses and
all-zero validation. A guard stops the run if validation mAP collapses to 0
after the warm-up epochs, instead of burning hours until patience runs out.

Examples
--------
    python scripts/training/train.py --data data/build/india_v3/data.yaml --profile laptop
    python scripts/training/train.py --data data/build/india_v3_pretrain/data.yaml --profile pretrain
    python scripts/training/train.py --data data/build/india_v3/data.yaml --profile finetune \
        --weights artifacts/runs/<pretrain_run>/weights/best.pt
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO_ROOT  # noqa: E402

RUNS_DIR = REPO_ROOT / "artifacts" / "runs"

# Augmentation + optimiser shared by all profiles (Ultralytics argument names).
COMMON_HYP = dict(
    optimizer="SGD",
    lr0=0.01,
    lrf=0.01,
    cos_lr=True,
    momentum=0.937,
    weight_decay=0.0005,
    warmup_epochs=3.0,
    mosaic=1.0,
    close_mosaic=15,
    mixup=0.1,
    degrees=3.0,
    translate=0.1,
    scale=0.5,
    shear=0.0,
    perspective=0.0,
    fliplr=0.5,
    flipud=0.0,
    hsv_h=0.015,
    hsv_s=0.7,
    hsv_v=0.4,
)

PROFILES = {
    "laptop": dict(model="yolo11s.pt", imgsz=768, epochs=150, batch=16, patience=40),
    "best": dict(model="yolo11m.pt", imgsz=768, epochs=150, batch=8, patience=40),
    "pretrain": dict(model="yolo11s.pt", imgsz=768, epochs=60, batch=16, patience=20),
    "finetune": dict(
        model=None, imgsz=768, epochs=60, batch=16, patience=20,
        lr0=0.002, warmup_epochs=1.0, close_mosaic=10, mixup=0.05,
    ),
    # Thin structures: no mixup, gentle scale, any rotation is valid for cracks.
    "crack_seg": dict(
        model="yolo11s-seg.pt", imgsz=448, epochs=120, batch=16, patience=30,
        mixup=0.0, scale=0.3, degrees=10.0, flipud=0.5,
    ),
}


def _collapse_guard(min_epoch: int = 5):
    """Stop training when losses go NaN or val mAP50 is exactly 0 after warm-up."""

    def on_fit_epoch_end(trainer):
        epoch = int(getattr(trainer, "epoch", 0)) + 1
        loss = getattr(trainer, "tloss", None)
        try:
            values = loss.tolist() if hasattr(loss, "tolist") else list(loss or [])
            values = values if isinstance(values, list) else [values]
        except TypeError:
            values = []
        bad_loss = any(isinstance(v, float) and math.isnan(v) for v in values)
        map50 = float((trainer.metrics or {}).get("metrics/mAP50(B)", -1))
        if bad_loss or (epoch >= min_epoch and map50 == 0.0):
            reason = "NaN loss" if bad_loss else f"val mAP50 == 0 at epoch {epoch}"
            print(
                f"\n[collapse-guard] Stopping: {reason}. Keep AMP off, lower --batch, "
                "and do not resume from this run's weights.\n"
            )
            trainer.stop = True

    return on_fit_epoch_end


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="data.yaml from build_dataset.py")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="laptop")
    ap.add_argument("--model", default=None, help="override base weights, e.g. yolo26s.pt / yolov8m.pt")
    ap.add_argument("--weights", type=Path, default=None, help="start weights (required for finetune)")
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--hours", type=float, default=None,
                    help="wall-clock budget; training stops in time and keeps best.pt "
                         "(e.g. 10.5 for a 12 h Kaggle session)")
    ap.add_argument("--batch", type=int, default=None, help="drop to 8/6 on CUDA OOM")
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=2, help="2 is stable on Windows")
    ap.add_argument("--amp", action="store_true", help="enable AMP (was unstable on this 4060)")
    ap.add_argument("--cache", choices=["none", "disk", "ram"], default="none")
    ap.add_argument("--name", default=None)
    ap.add_argument("--project", type=Path, default=RUNS_DIR, help="runs folder")
    ap.add_argument("--resume", type=Path, default=None, help="resume a run from its last.pt")
    ap.add_argument("--dry-run", action="store_true", help="print the resolved config only")
    args = ap.parse_args(argv)

    profile = dict(PROFILES[args.profile])
    hyp = {**COMMON_HYP, **{k: v for k, v in profile.items() if k in COMMON_HYP}}
    base = args.model or (str(args.weights) if args.weights else None) or profile["model"]
    if args.profile == "finetune" and not args.weights and not args.model:
        ap.error("--profile finetune needs --weights <best.pt from a previous run>")
    if not args.data.exists():
        ap.error(f"{args.data} not found — run scripts/training/build_dataset.py first")

    name = args.name or f"india_{args.profile}_{datetime.now():%Y%m%d_%H%M}"
    train_args = dict(
        data=str(args.data.resolve()),
        epochs=args.epochs or profile["epochs"],
        imgsz=args.imgsz or profile["imgsz"],
        batch=args.batch or profile["batch"],
        patience=profile["patience"],
        device=args.device,
        workers=args.workers,
        amp=bool(args.amp),
        cache=False if args.cache == "none" else args.cache,
        project=str(args.project.resolve()),
        name=name,
        exist_ok=False,
        seed=42,
        plots=True,
        val=True,
        **hyp,
    )
    if args.hours:
        train_args["time"] = float(args.hours)  # Ultralytics: overrides epochs, schedule fits the budget
    print(json.dumps({"base_weights": base, "profile": args.profile, **train_args}, indent=2))
    if args.dry_run:
        return 0

    from ultralytics import YOLO

    if args.resume:
        model = YOLO(str(args.resume))
        model.add_callback("on_fit_epoch_end", _collapse_guard())
        model.train(resume=True)
        return 0

    model = YOLO(base)
    model.add_callback("on_fit_epoch_end", _collapse_guard())
    model.train(**train_args)

    run_dir = args.project.resolve() / name
    card = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "profile": args.profile,
        "base_weights": base,
        "train_args": train_args,
    }
    report = args.data.parent / "build_report.json"
    if report.exists():
        card["dataset"] = json.loads(report.read_text(encoding="utf-8"))
    (run_dir / "run_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(f"\nDone. Weights: {run_dir / 'weights' / 'best.pt'}")
    print("Next: python scripts/training/evaluate.py --candidate", run_dir / "weights" / "best.pt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
