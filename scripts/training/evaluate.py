"""A/B gate a candidate model against production on Indian roads, then promote.

Steps
-----
1. Validate candidate and baseline (``artifacts/ai_models/best.pt``) on the
   RDD India val split — each at its own training image size, as deployed.
2. Optional: also on ``local_val.yaml`` (your municipality's labelled photos).
3. Tune one confidence threshold per class on the candidate's val curves,
   maximising F-beta (beta=1.5 favours recall: a missed pothole costs more than
   a false alarm, which a reviewer rejects in seconds).
4. Gate (all must pass):
      * India val mAP50 >= baseline mAP50
      * pothole AP50 >= baseline pothole AP50 - 0.02
      * mean recall >= baseline mean recall - 0.02
      * local val mAP50 >= baseline (when local_val.yaml exists)
5. ``--promote`` (only if the gate passes, or with ``--force``): back up the
   current best.pt, copy the candidate in, and write ``best.model.json``
   (image size + per-class thresholds + metrics) which the Flask app reads.

Example
-------
    python scripts/training/evaluate.py --candidate artifacts/runs/<run>/weights/best.pt \
        --data data/build/india_v3/data.yaml --promote
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import APP_MODELS, CLASS_NAMES, DEFAULT_BUILD  # noqa: E402

POTHOLE = 3


def train_imgsz(model) -> int:
    try:
        return int((model.ckpt or {}).get("train_args", {}).get("imgsz") or 640)
    except Exception:
        return 640


def validate(weights: Path, data: Path, split: str, device: str, out_dir: Path, tag: str) -> dict:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    imgsz = train_imgsz(model)
    r = model.val(
        data=str(data), split=split, imgsz=imgsz, batch=8, device=device,
        plots=False, verbose=False, project=str(out_dir), name=tag, exist_ok=True,
        conf=0.001, iou=0.6,
    )
    box = r.box
    per_class = {}
    for row, cid in enumerate(box.ap_class_index):
        cid = int(cid)
        per_class[CLASS_NAMES[cid]] = {
            "ap50": round(float(box.ap50[row]), 4),
            "ap50_95": round(float(box.ap[row]), 4),
            "precision": round(float(box.p[row]), 4),
            "recall": round(float(box.r[row]), 4),
        }
    return {
        "weights": str(weights),
        "imgsz": imgsz,
        "split": split,
        "map50": round(float(box.map50), 4),
        "map50_95": round(float(box.map), 4),
        "precision": round(float(box.mp), 4),
        "recall": round(float(box.mr), 4),
        "per_class": per_class,
        "_curves": {
            "px": np.asarray(box.px),
            "p": np.asarray(box.p_curve),
            "r": np.asarray(box.r_curve),
            "class_index": [int(c) for c in box.ap_class_index],
        },
    }


def tune_thresholds(curves: dict, beta: float, lo: float = 0.10, hi: float = 0.6) -> dict:
    px, p, r = curves["px"], curves["p"], curves["r"]
    window = (px >= lo) & (px <= hi)
    out = {}
    for row, cid in enumerate(curves["class_index"]):
        pc, rc = p[row], r[row]
        b2 = beta * beta
        fb = (1 + b2) * pc * rc / np.maximum(b2 * pc + rc, 1e-9)
        fb = np.where(window, fb, -1)
        best = int(np.argmax(fb))
        out[CLASS_NAMES[cid]] = {
            "conf": round(float(px[best]), 3),
            "precision": round(float(pc[best]), 3),
            "recall": round(float(rc[best]), 3),
            f"f{beta:g}": round(float(fb[best]), 3),
        }
    return out


def _ap50(metrics: dict, cid: int) -> float:
    return float(metrics["per_class"].get(CLASS_NAMES[cid], {}).get("ap50", 0.0))


def gate(cand: dict, base: dict, cand_local: dict | None, base_local: dict | None) -> list[dict]:
    checks = [
        {
            "check": "India val mAP50 >= baseline",
            "candidate": cand["map50"], "baseline": base["map50"],
            "pass": cand["map50"] >= base["map50"],
        },
        {
            "check": "pothole AP50 >= baseline - 0.02",
            "candidate": _ap50(cand, POTHOLE), "baseline": _ap50(base, POTHOLE),
            "pass": _ap50(cand, POTHOLE) >= _ap50(base, POTHOLE) - 0.02,
        },
        {
            "check": "mean recall >= baseline - 0.02",
            "candidate": cand["recall"], "baseline": base["recall"],
            "pass": cand["recall"] >= base["recall"] - 0.02,
        },
    ]
    if cand_local and base_local:
        checks.append({
            "check": "local val mAP50 >= baseline",
            "candidate": cand_local["map50"], "baseline": base_local["map50"],
            "pass": cand_local["map50"] >= base_local["map50"],
        })
    return checks


def _table(cand: dict, base: dict) -> str:
    lines = [
        "| Metric | Baseline | Candidate | Change |",
        "|---|---|---|---|",
    ]
    for key in ("map50", "map50_95", "precision", "recall"):
        b, c = base[key], cand[key]
        lines.append(f"| {key} | {b:.3f} | {c:.3f} | {c - b:+.3f} |")
    for name in CLASS_NAMES.values():
        b = base["per_class"].get(name, {}).get("ap50", 0.0)
        c = cand["per_class"].get(name, {}).get("ap50", 0.0)
        lines.append(f"| AP50 {name} | {b:.3f} | {c:.3f} | {c - b:+.3f} |")
    return "\n".join(lines)


def promote(candidate: Path, card: dict) -> Path:
    APP_MODELS.mkdir(parents=True, exist_ok=True)
    target = APP_MODELS / "best.pt"
    if target.exists():
        backup = APP_MODELS / f"best_backup_{datetime.now():%Y%m%d_%H%M%S}.pt"
        shutil.copy2(target, backup)
        old_card = APP_MODELS / "best.model.json"
        if old_card.exists():
            shutil.copy2(old_card, backup.with_suffix(".model.json"))
        print(f"Backed up previous model -> {backup.name}")
    shutil.copy2(candidate, target)
    (APP_MODELS / "best.model.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    return target


def _public(metrics: dict | None) -> dict | None:
    return None if metrics is None else {k: v for k, v in metrics.items() if k != "_curves"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, default=APP_MODELS / "best.pt")
    ap.add_argument("--data", type=Path, default=DEFAULT_BUILD / "india_v3" / "data.yaml")
    ap.add_argument("--local-data", type=Path, default=None,
                    help="local_val.yaml (defaults to the one next to --data if present)")
    ap.add_argument("--device", default="0")
    ap.add_argument("--beta", type=float, default=1.5, help="F-beta for threshold tuning")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--force", action="store_true", help="promote even if the gate fails")
    args = ap.parse_args(argv)

    out_dir = args.candidate.resolve().parents[1] / "evaluation"
    out_dir.mkdir(parents=True, exist_ok=True)
    local = args.local_data or (args.data.parent / "local_val.yaml")
    local = local if local.exists() else None

    print("Validating candidate / baseline on India val ...")
    cand = validate(args.candidate, args.data, "val", args.device, out_dir, "candidate_val")
    base = validate(args.baseline, args.data, "val", args.device, out_dir, "baseline_val")
    cand_local = base_local = None
    if local:
        cand_local = validate(args.candidate, local, "val", args.device, out_dir, "candidate_local")
        base_local = validate(args.baseline, local, "val", args.device, out_dir, "baseline_local")
    cand_test = validate(args.candidate, args.data, "test", args.device, out_dir, "candidate_test")

    thresholds = tune_thresholds(cand["_curves"], args.beta)
    checks = gate(cand, base, cand_local, base_local)
    passed = all(c["pass"] for c in checks)

    md = [
        f"# Evaluation — {args.candidate}",
        f"Generated {datetime.now():%Y-%m-%d %H:%M}. Baseline: `{args.baseline}`.",
        "",
        "## RDD India val",
        _table(cand, base),
        "",
        f"Candidate on held-out India **test**: mAP50 {cand_test['map50']:.3f}, "
        f"recall {cand_test['recall']:.3f}.",
        "",
        "## Gate",
        *[f"- {'PASS' if c['pass'] else 'FAIL'}: {c['check']} "
          f"(candidate {c['candidate']:.3f} vs {c['baseline']:.3f})" for c in checks],
        "",
        f"## Per-class thresholds (F{args.beta:g} on val)",
        *[f"- {name}: conf {t['conf']} -> precision {t['precision']}, recall {t['recall']}"
          for name, t in thresholds.items()],
    ]
    report = "\n".join(md)
    (out_dir / "evaluation.md").write_text(report, encoding="utf-8")
    result = {
        "candidate": _public(cand), "baseline": _public(base),
        "candidate_local": _public(cand_local), "baseline_local": _public(base_local),
        "candidate_test": _public(cand_test), "thresholds": thresholds,
        "gate": checks, "passed": passed,
    }
    (out_dir / "evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("\n" + report)
    print(f"\nGATE {'PASSED' if passed else 'FAILED'} — report: {out_dir / 'evaluation.md'}")

    if args.promote:
        if not passed and not args.force:
            print("Not promoting: gate failed (use --force to override).")
            return 1
        run_card_path = args.candidate.resolve().parents[1] / "run_card.json"
        card = {
            "model_file": "best.pt",
            "promoted_at": datetime.now().isoformat(timespec="seconds"),
            "source_weights": str(args.candidate.resolve()),
            "imgsz": cand["imgsz"],
            "class_thresholds": {name: t["conf"] for name, t in thresholds.items()},
            "threshold_method": f"F{args.beta:g} on RDD India val",
            "metrics_india_val": _public(cand),
            "metrics_india_test": _public(cand_test),
            "metrics_local_val": _public(cand_local),
            "gate": checks,
            "gate_passed": passed,
            "run_card": json.loads(run_card_path.read_text(encoding="utf-8"))
            if run_card_path.exists() else None,
        }
        target = promote(args.candidate, card)
        print(f"Promoted -> {target} (+ best.model.json). Restart Flask to load it.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
