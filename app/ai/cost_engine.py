"""Quantity x unit-rate repair cost estimation (INR).

Replaces the old flat (damage type x severity) lookup. For each detection:

* pothole   -> cut-and-patch volume (m3): patch footprint extended by a cutting
               margin on every side x assumed/measured depth
* alligator -> patch / overlay / reconstruction area (m2), margin included
* cracks    -> sealing length (running metres)

Unit rates are municipality-specific. Defaults below are **placeholders** to be
replaced with the municipality's Schedule of Rates: store a JSON object under
``system_settings.setting_key = 'repair_cost_rates'`` with any subset of the
keys in ``DEFAULT_COST_RATES`` and it overrides these values.

When the size estimate is unreliable, a typical-size quantity for the severity
band is used and the result is flagged ``basis = typical_size_fallback``.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

COST_RULE_VERSION = "cost-v2-quantity"

DEFAULT_COST_RATES: dict[str, Any] = {
    "currency": "INR",
    "source": "placeholder_defaults",
    # Cut square edges this far beyond the visible damage on every side.
    "patch_margin_m": 0.15,
    "rates": {
        # Bituminous patching (cut, tack coat, hot/cold mix, compaction) per m3.
        "pothole_patch_per_m3": 12000.0,
        # Crack cleaning + sealing per running metre.
        "crack_seal_per_m": {"MINOR": 120.0, "MODERATE": 180.0, "SEVERE": 300.0},
        # Alligator: MINOR seal coat, MODERATE patch/overlay, SEVERE full-depth.
        "alligator_per_m2": {"MINOR": 180.0, "MODERATE": 650.0, "SEVERE": 1800.0},
        # Anything unclassified, per m2 of affected area.
        "default_per_m2": {"MINOR": 200.0, "MODERATE": 600.0, "SEVERE": 1500.0},
    },
    # Floor per repair item (crew visit, materials minimum).
    "minimum_item_cost": 500.0,
    # Applied once per inspection site with at least one item.
    "mobilisation_per_site": 2000.0,
    "contingency_pct": 10.0,
    # Typical quantities when the size estimate is unreliable.
    "typical_size": {
        "pothole_diameter_m": {"MINOR": 0.25, "MODERATE": 0.4, "SEVERE": 0.75},
        "pothole_depth_mm": {"MINOR": 20.0, "MODERATE": 35.0, "SEVERE": 75.0},
        "crack_length_m": {"MINOR": 1.0, "MODERATE": 3.0, "SEVERE": 6.0},
        "area_m2": {"MINOR": 0.3, "MODERATE": 1.5, "SEVERE": 4.0},
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def get_cost_rates() -> dict[str, Any]:
    rates = copy.deepcopy(DEFAULT_COST_RATES)
    try:
        from app.ai.severity_rules import load_db_setting

        db_rates = load_db_setting("repair_cost_rates")
        if isinstance(db_rates, dict) and db_rates:
            _deep_merge(rates, db_rates)
            rates["source"] = db_rates.get("source") or "system_settings"
    except Exception:
        logger.warning("repair_cost_rates unavailable; using placeholder defaults")
    return rates


def _band(table: Any, level: str, default: float) -> float:
    if isinstance(table, dict):
        value = table.get(level, table.get("MODERATE", default))
    else:
        value = table
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def estimate_detection_cost(
    family: str,
    level: str,
    measurements: dict[str, Any] | None,
    *,
    rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cost estimate for one detection with the quantity it was derived from."""
    cfg = rates or get_cost_rates()
    unit_rates = cfg.get("rates") or {}
    typical = cfg.get("typical_size") or {}
    margin = float(cfg.get("patch_margin_m", 0.15))
    level = str(level or "MODERATE").upper()
    m = measurements or {}
    reliable = bool(m.get("reliable")) and m.get("width_m") is not None
    basis = "estimated_size" if reliable else "typical_size_fallback"

    width = _f(m.get("width_m"))
    length = _f(m.get("length_m"))
    fill = _f(m.get("fill_factor")) or 1.0

    if family == "pothole":
        if not reliable:
            dia = _band(typical.get("pothole_diameter_m"), level, 0.4)
            width = length = dia
        depth_mm = _f(m.get("estimated_depth_mm")) or _band(
            typical.get("pothole_depth_mm"), level, 35.0
        )
        patch_area = (width + 2 * margin) * (length + 2 * margin)
        quantity = patch_area * depth_mm / 1000.0
        unit, rate = "m3", _band(unit_rates.get("pothole_patch_per_m3"), level, 12000.0)
        detail = (
            f"patch {width + 2 * margin:.2f} x {length + 2 * margin:.2f} m "
            f"x {depth_mm:.0f} mm depth (assumed)"
        )
    elif family in {"longitudinal", "transverse", "crack"}:
        quantity = _f(m.get("crack_length_m")) if reliable else None
        if quantity is None:
            quantity = _band(typical.get("crack_length_m"), level, 3.0)
        unit, rate = "m", _band(unit_rates.get("crack_seal_per_m"), level, 180.0)
        detail = f"seal {quantity:.2f} running m"
    else:
        table_key = "alligator_per_m2" if family == "alligator" else "default_per_m2"
        if reliable:
            # Affected area plus cutting margin around the distressed zone.
            quantity = (width + 2 * margin) * (length + 2 * margin) * min(1.0, fill + 0.1)
        else:
            quantity = _band(typical.get("area_m2"), level, 1.5)
        unit, rate = "m2", _band(unit_rates.get(table_key), level, 600.0)
        detail = f"treat {quantity:.2f} m2 incl. margin"

    raw = quantity * rate
    floor = float(cfg.get("minimum_item_cost", 0.0))
    amount = max(raw, floor)
    return {
        "amount": round(amount, 2),
        "currency": cfg.get("currency", "INR"),
        "quantity": round(quantity, 4),
        "unit": unit,
        "unit_rate": rate,
        "minimum_applied": raw < floor,
        "basis": basis,
        "detail": detail,
        "rate_source": cfg.get("source"),
        "rule_version": COST_RULE_VERSION,
    }


def _review_min_confidence() -> float:
    try:
        from app.ai.severity_rules import get_severity_rules

        return float(get_severity_rules().get("confidence_min_review", 0.35))
    except Exception:
        return 0.35


def cost_counts_in_total(det: dict[str, Any], min_confidence: float) -> bool | None:
    """True = priced into the inspection total, None = waiting for review, False = rejected.

    A reviewer's decision always wins; without one, only detections at or above the
    manual-review confidence are counted, so unconfirmed guesses cannot inflate totals.
    """
    status = str(det.get("status") or "").upper()
    if status == "REJECTED" or det.get("is_false_positive"):
        return False
    if status == "APPROVED" or det.get("is_verified"):
        return True
    conf = _f(det.get("confidence"))
    if conf is None:
        conf = _f(det.get("confidence_score"))
    if conf is None or conf >= min_confidence:
        return True
    return None


def summarize_costs(
    detections: list[dict[str, Any]],
    *,
    rates: dict[str, Any] | None = None,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    """Inspection total = counted items x (1 + contingency) + one mobilisation charge.

    Counted items are reviewer-approved detections plus unreviewed ones at or above
    the manual-review confidence. Low-confidence ones are reported as
    ``pending_review_*`` and join the total once a reviewer approves them; rejected
    ones are dropped.
    """
    cfg = rates or get_cost_rates()
    threshold = _review_min_confidence() if min_confidence is None else float(min_confidence)
    items_total = pending_total = 0.0
    counted = pending = rejected = fallback_items = 0
    for det in detections:
        cost = det.get("cost_estimate") or {}
        amount = _f(cost.get("amount"))
        if amount is None:
            amount = _f(det.get("estimated_cost")) or 0.0
        verdict = cost_counts_in_total(det, threshold)
        if verdict is False:
            rejected += 1
            continue
        if verdict is None:
            pending += 1
            pending_total += amount
            continue
        counted += 1
        items_total += amount
        if cost.get("basis") == "typical_size_fallback":
            fallback_items += 1
    contingency_pct = float(cfg.get("contingency_pct", 0.0))
    contingency = items_total * contingency_pct / 100.0
    mobilisation = float(cfg.get("mobilisation_per_site", 0.0)) if counted else 0.0
    total = items_total + contingency + mobilisation if counted else 0.0
    return {
        "items_total": round(items_total, 2),
        "contingency_pct": contingency_pct,
        "contingency": round(contingency, 2),
        "mobilisation": round(mobilisation, 2),
        "total": round(total, 2),
        "currency": cfg.get("currency", "INR"),
        "item_count": counted,
        "fallback_items": fallback_items,
        "pending_review_count": pending,
        "pending_review_items_total": round(pending_total, 2),
        "rejected_count": rejected,
        "review_min_confidence": threshold,
        "rate_source": cfg.get("source"),
        "rule_version": COST_RULE_VERSION,
    }


__all__ = [
    "DEFAULT_COST_RATES",
    "cost_counts_in_total",
    "estimate_detection_cost",
    "get_cost_rates",
    "summarize_costs",
]
