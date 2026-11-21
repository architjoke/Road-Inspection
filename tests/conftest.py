"""Shared offline pytest fixtures — no RDS/AWS/GPU required."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

# Force mock detector for the entire test session unless overridden.
os.environ.setdefault("YOLO_FORCE_MOCK", "true")
os.environ.setdefault("ANALYSIS_MAX_WORKERS", "2")


@pytest.fixture
def tmp_image(tmp_path: Path) -> Path:
    path = tmp_path / "road.jpg"
    Image.new("RGB", (1280, 720), color=(60, 60, 60)).save(path, format="JPEG")
    return path


@pytest.fixture
def corrupted_image(tmp_path: Path) -> Path:
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not-an-image")
    return path


@pytest.fixture(autouse=True)
def _reset_stores():
    from app.ai.depth_estimation import reset_depth_cache
    from app.ai.model_loader import reset_model_cache
    from app.ai.segmentation import reset_segmentation_cache
    from app.services.detection_service import clear_memory_detections
    from app.services.comparison_service import clear_comparisons
    from app.services.report_service import clear_report_jobs
    from app.services.audit_trail import clear_audit_events
    from app.services.directory_service import clear_directory
    from app.services.offline_store import offline_store
    from app.services.repair_service import clear_repairs
    from app.workers.executor import reset_executor_for_tests
    from app.workers.task_store import task_store

    reset_model_cache()
    reset_segmentation_cache()
    reset_depth_cache()
    clear_memory_detections()
    clear_comparisons()
    clear_report_jobs()
    clear_audit_events()
    task_store.clear()
    offline_store.clear()
    clear_directory()
    clear_repairs()
    yield
    reset_model_cache()
    reset_segmentation_cache()
    reset_depth_cache()
    clear_memory_detections()
    clear_comparisons()
    clear_report_jobs()
    clear_audit_events()
    task_store.clear()
    offline_store.clear()
    clear_directory()
    clear_repairs()
    reset_executor_for_tests()
