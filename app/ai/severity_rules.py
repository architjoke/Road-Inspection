"""Configurable severity rule definitions.

v2 grades damage on *estimated physical size* (see ``ground_geometry``):

* pothole    -> average diameter (mm)   — thresholds from ``system_settings``
                ``severity_thresholds`` (minor <= 250 mm, severe > 500 mm)
* alligator  -> affected area (m2)
* long./trans. cracks -> crack length (m)

Model confidence is **not** a severity signal; it only drives manual review.
Code defaults apply when the DB row is missing, offline, or under tests.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

RULE_VERSION = "phase10-v2-geometry"

SEVERITY_LEVELS = ("MINOR", "MODERATE", "SEVERE")

DEFAULT_SEVERITY_RULES: dict[str, Any] = {
    "rule_version": RULE_VERSION,
    "confidence_min_review": 0.35,
    "confidence_ambiguous": 0.45,
    "priority_map": {"SEVERE": 1, "MODERATE": 2, "MINOR": 3},
    "actions": {
        "SEVERE": "emergency inspection",
        "MODERATE": "priority repair",
        "MINOR": "scheduled monitoring",
    },
    "repair_types": {
        "pothole": {
            "SEVERE": "full-depth patch / cold or hot mix repair",
            "MODERATE": "surface patch",
            "MINOR": "monitor / temporary fill",
        },
        "alligator": {
            "SEVERE": "section reconstruction / mill-and-overlay review",
            "MODERATE": "localized overlay / crack seal prep",
            "MINOR": "seal and monitor",
        },
        "longitudinal": {
            "SEVERE": "crack seal + structural review",
            "MODERATE": "crack sealing",
            "MINOR": "monitor crack growth",
        },
        "transverse": {
            "SEVERE": "crack seal + structural review",
            "MODERATE": "crack sealing",
            "MINOR": "monitor crack growth",
        },
        "crack": {
            "SEVERE": "crack seal + structural review",
            "MODERATE": "crack sealing",
            "MINOR": "monitor crack growth",
        },
        "default": {
            "SEVERE": "priority field inspection",
            "MODERATE": "scheduled repair review",
            "MINOR": "routine monitoring",
        },
    },
    # Continuous score bands (used for ranking within a level).
    "score_thresholds": {"severe": 0.72, "moderate": 0.42},
    # Per-family physical grading. ``moderate`` / ``severe`` are lower bounds.
    "classes": {
        "pothole": {
            "measure": "diameter_mm",
            "moderate": 250.0,
            "severe": 500.0,
            # Pothole outline vs its bounding box (ellipse inscribed in a box).
            "fill_factor": 0.785,
            # Assumed depth for volume/cost when depth is not measured.
            "assumed_depth_mm": {"MINOR": 20.0, "MODERATE": 35.0, "SEVERE": 75.0},
            "severe_rel_depth": 0.75,
            "severe_bbox_norm_area": 0.08,
            "moderate_bbox_norm_area": 0.025,
        },
        "alligator": {
            "measure": "area_m2",
            "moderate": 0.5,
            "severe": 3.0,
            "fill_factor": 0.8,
            "severe_rel_depth": 0.80,
            "severe_bbox_norm_area": 0.12,
            "moderate_bbox_norm_area": 0.04,
        },
        "longitudinal": {
            "measure": "length_m",
            "moderate": 2.0,
            "severe": 5.0,
            "severe_rel_depth": 0.85,
            "severe_bbox_norm_area": 0.06,
            "moderate_bbox_norm_area": 0.02,
        },
        "transverse": {
            "measure": "length_m",
            "moderate": 2.0,
            "severe": 5.0,
            "severe_rel_depth": 0.85,
            "severe_bbox_norm_area": 0.06,
            "moderate_bbox_norm_area": 0.02,
        },
        "crack": {
            "measure": "length_m",
            "moderate": 2.0,
            "severe": 5.0,
            "severe_rel_depth": 0.85,
            "severe_bbox_norm_area": 0.05,
            "moderate_bbox_norm_area": 0.015,
        },
        "default": {
            "measure": "area_m2",
            "moderate": 0.5,
            "severe": 3.0,
            "fill_factor": 0.7,
            "severe_rel_depth": 0.85,
            "severe_bbox_norm_area": 0.10,
            "moderate_bbox_norm_area": 0.03,
        },
    },
}

_DB_CACHE_TTL_SEC = 300.0
_db_cache_lock = threading.Lock()
_db_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default



def _db_settings_allowed() -> bool:
    try:
        from flask import current_app, has_app_context

        if not has_app_context() or current_app.config.get("TESTING"):
            return False
        from app.services.offline_store import offline_mode_enabled

        return not offline_mode_enabled()
    except Exception:
        return False


def load_db_setting(key: str) -> Any:
    """Read one ``system_settings.setting_value`` (cached; None on any failure)."""
    if not _db_settings_allowed():
        return None
    now = time.monotonic()
    with _db_cache_lock:
        cached = _db_cache.get("value")
        if cached is not None and now - _db_cache["at"] < _DB_CACHE_TTL_SEC:
            return cached.get(key)
    values: dict[str, Any] = {}
    try:
        from sqlalchemy import text

        from app.extensions import db

        rows = db.session.execute(
            text(
                "SELECT setting_key, setting_value FROM system_settings "
                "WHERE setting_key IN ('severity_thresholds', 'default_priority_mapping', "
                "'repair_cost_rates', 'ssim_repair_verified_threshold', "
                "'ssim_manual_review_threshold')"
            )
        ).fetchall()
        values = {r[0]: r[1] for r in rows}
    except Exception:
        logger.warning("system_settings unavailable; using code severity defaults")
        try:
            from app.extensions import db

            db.session.rollback()
        except Exception:
            pass
    with _db_cache_lock:
        _db_cache["at"] = now
        _db_cache["value"] = values
    return values.get(key)


def _apply_db_thresholds(rules: dict[str, Any]) -> None:
    thresholds = load_db_setting("severity_thresholds")
    if isinstance(thresholds, dict):
        pothole = rules["classes"]["pothole"]
        minor = thresholds.get("minor") or {}
        moderate = thresholds.get("moderate") or {}
        severe = thresholds.get("severe") or {}
        try:
            if minor.get("max_diagonal_mm") is not None:
                pothole["moderate"] = float(minor["max_diagonal_mm"])
            if severe.get("min_diagonal_mm") is not None:
                pothole["severe"] = float(severe["min_diagonal_mm"])
            depth = pothole["assumed_depth_mm"]
            if minor.get("max_depth_mm") is not None:
                depth["MINOR"] = float(minor["max_depth_mm"])
            if minor.get("max_depth_mm") is not None and moderate.get("max_depth_mm") is not None:
                depth["MODERATE"] = (
                    float(minor["max_depth_mm"]) + float(moderate["max_depth_mm"])
                ) / 2.0
            if severe.get("min_depth_mm") is not None:
                depth["SEVERE"] = float(severe["min_depth_mm"]) * 1.5
            rules["threshold_source"] = "system_settings"
        except (TypeError, ValueError):
            logger.warning("Malformed severity_thresholds row; using defaults")

    mapping = load_db_setting("default_priority_mapping")
    if isinstance(mapping, dict):
        try:
            rules["priority_map"] = {
                str(k).upper(): int(v) for k, v in mapping.items()
            }
        except (TypeError, ValueError):
            pass


def get_severity_rules() -> dict[str, Any]:
    """Deep copy of rules with DB (system_settings) + env/app-config overrides."""
    rules = copy.deepcopy(DEFAULT_SEVERITY_RULES)
    rules["threshold_source"] = "code_defaults"
    _apply_db_thresholds(rules)

    try:
        from flask import current_app, has_app_context

        if has_app_context():
            if current_app.config.get("SEVERITY_CONFIDENCE_MIN_REVIEW") is not None:
                rules["confidence_min_review"] = float(
                    current_app.config["SEVERITY_CONFIDENCE_MIN_REVIEW"]
                )
            if current_app.config.get("SEVERITY_CONFIDENCE_AMBIGUOUS") is not None:
                rules["confidence_ambiguous"] = float(
                    current_app.config["SEVERITY_CONFIDENCE_AMBIGUOUS"]
                )
    except Exception:
        pass

    if os.getenv("SEVERITY_CONFIDENCE_MIN_REVIEW") is not None:
        rules["confidence_min_review"] = _env_float(
            "SEVERITY_CONFIDENCE_MIN_REVIEW", rules["confidence_min_review"]
        )
    if os.getenv("SEVERITY_CONFIDENCE_AMBIGUOUS") is not None:
        rules["confidence_ambiguous"] = _env_float(
            "SEVERITY_CONFIDENCE_AMBIGUOUS", rules["confidence_ambiguous"]
        )
    # Old phase10-v1 env values (e.g. SEVERITY_RULE_VERSION=phase10-v1 in .env)
    # must not mislabel the new engine, so only non-v1 overrides are honoured.
    env_version = os.getenv("SEVERITY_RULE_VERSION")
    if env_version and env_version != "phase10-v1":
        rules["rule_version"] = env_version

    return rules


def classify_damage_family(class_name: str | None, damage_type: str | None = None) -> str:
    text = f"{class_name or ''} {damage_type or ''}".lower()
    if "pothole" in text or "d40" in text:
        return "pothole"
    if "alligator" in text or "d20" in text:
        return "alligator"
    if "longitudinal" in text or "d00" in text:
        return "longitudinal"
    if "transverse" in text or "lateral" in text or "d10" in text:
        return "transverse"
    if "crack" in text:
        return "crack"
    return "default"
