"""Configurable severity rule definitions (file/env backed; no RDS required)."""

from __future__ import annotations

import copy
import os
from typing import Any


RULE_VERSION = "phase10-v1"

SEVERITY_LEVELS = ("MINOR", "MODERATE", "SEVERE")

# Default hybrid rules — single source of truth for Phase 10.
# Env overrides adjust key globals; class tables stay here for clarity.
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
    # Score weights for hybrid evidence (relative depth never becomes metric)
    "weights": {
        "confidence": 0.35,
        "bbox_area": 0.25,
        "segmentation_area": 0.30,
        "relative_depth": 0.10,
    },
    "score_thresholds": {
        "severe": 0.72,
        "moderate": 0.42,
    },
    "classes": {
        "pothole": {
            "severe_area_pct": 5.0,
            "moderate_area_pct": 1.5,
            "severe_bbox_norm_area": 0.08,
            "moderate_bbox_norm_area": 0.025,
            "severe_rel_depth": 0.75,
            "moderate_rel_depth": 0.55,
            "severe_confidence": 0.35,
            "moderate_confidence": 0.25,
        },
        "alligator": {
            "severe_area_pct": 8.0,
            "moderate_area_pct": 3.0,
            "severe_bbox_norm_area": 0.12,
            "moderate_bbox_norm_area": 0.04,
            "severe_rel_depth": 0.80,
            "moderate_rel_depth": 0.60,
            "severe_confidence": 0.45,
            "moderate_confidence": 0.30,
        },
        "longitudinal": {
            "severe_area_pct": 6.0,
            "moderate_area_pct": 2.0,
            "severe_bbox_norm_area": 0.06,
            "moderate_bbox_norm_area": 0.02,
            "severe_rel_depth": 0.85,
            "moderate_rel_depth": 0.65,
            "severe_confidence": 0.60,
            "moderate_confidence": 0.40,
            "elongation_severe": 4.0,
        },
        "transverse": {
            "severe_area_pct": 6.0,
            "moderate_area_pct": 2.0,
            "severe_bbox_norm_area": 0.06,
            "moderate_bbox_norm_area": 0.02,
            "severe_rel_depth": 0.85,
            "moderate_rel_depth": 0.65,
            "severe_confidence": 0.60,
            "moderate_confidence": 0.40,
            "elongation_severe": 4.0,
        },
        "crack": {
            "severe_area_pct": 5.0,
            "moderate_area_pct": 2.0,
            "severe_bbox_norm_area": 0.05,
            "moderate_bbox_norm_area": 0.015,
            "severe_rel_depth": 0.85,
            "moderate_rel_depth": 0.65,
            "severe_confidence": 0.60,
            "moderate_confidence": 0.40,
        },
        "default": {
            "severe_area_pct": 8.0,
            "moderate_area_pct": 3.0,
            "severe_bbox_norm_area": 0.10,
            "moderate_bbox_norm_area": 0.03,
            "severe_rel_depth": 0.85,
            "moderate_rel_depth": 0.65,
            "severe_confidence": 0.70,
            "moderate_confidence": 0.45,
        },
    },
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_severity_rules() -> dict[str, Any]:
    """Return a deep copy of rules with optional env overrides."""
    rules = copy.deepcopy(DEFAULT_SEVERITY_RULES)

    try:
        from flask import current_app, has_app_context

        if has_app_context():
            if current_app.config.get("SEVERITY_CONFIDENCE_MIN_REVIEW") is not None:
                rules["confidence_min_review"] = float(
                    current_app.config["SEVERITY_CONFIDENCE_MIN_REVIEW"]
                )
            if current_app.config.get("SEVERITY_RULE_VERSION"):
                rules["rule_version"] = str(current_app.config["SEVERITY_RULE_VERSION"])
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
    if os.getenv("SEVERITY_SCORE_SEVERE") is not None:
        rules["score_thresholds"]["severe"] = _env_float(
            "SEVERITY_SCORE_SEVERE", rules["score_thresholds"]["severe"]
        )
    if os.getenv("SEVERITY_SCORE_MODERATE") is not None:
        rules["score_thresholds"]["moderate"] = _env_float(
            "SEVERITY_SCORE_MODERATE", rules["score_thresholds"]["moderate"]
        )
    if os.getenv("SEVERITY_RULE_VERSION"):
        rules["rule_version"] = os.getenv("SEVERITY_RULE_VERSION") or RULE_VERSION

    try:
        from flask import current_app, has_app_context

        if has_app_context():
            if current_app.config.get("SEVERITY_SCORE_SEVERE") is not None:
                rules["score_thresholds"]["severe"] = float(
                    current_app.config["SEVERITY_SCORE_SEVERE"]
                )
            if current_app.config.get("SEVERITY_SCORE_MODERATE") is not None:
                rules["score_thresholds"]["moderate"] = float(
                    current_app.config["SEVERITY_SCORE_MODERATE"]
                )
    except Exception:
        pass

    return rules


def classify_damage_family(class_name: str | None, damage_type: str | None = None) -> str:
    text = f"{class_name or ''} {damage_type or ''}".lower()
    if "pothole" in text or "d40" in text:
        return "pothole"
    if "alligator" in text or "d20" in text:
        return "alligator"
    if "longitudinal" in text or "d00" in text:
        return "longitudinal"
    if "transverse" in text or "d10" in text:
        return "transverse"
    if "crack" in text:
        return "crack"
    return "default"
