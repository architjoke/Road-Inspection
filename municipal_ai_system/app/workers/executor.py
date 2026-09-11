"""Bounded ThreadPoolExecutor for Phase 6 analysis jobs."""

from __future__ import annotations

import atexit
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_max_workers = max(1, int(os.getenv("ANALYSIS_MAX_WORKERS", "2")))
_atexit_registered = False


def get_executor() -> ThreadPoolExecutor:
    global _executor, _atexit_registered
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=_max_workers,
                thread_name_prefix="analysis-worker",
            )
            if not _atexit_registered:
                atexit.register(shutdown_executor)
                _atexit_registered = True
            logger.info(
                "Analysis ThreadPoolExecutor started (max_workers=%s)",
                _max_workers,
            )
        return _executor


def submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    return get_executor().submit(fn, *args, **kwargs)


def shutdown_executor(wait: bool = False) -> None:
    global _executor
    with _lock:
        if _executor is not None:
            logger.info("Shutting down analysis executor (wait=%s)", wait)
            try:
                _executor.shutdown(wait=wait, cancel_futures=False)
            except TypeError:
                # Python < 3.9 compatibility
                _executor.shutdown(wait=wait)
            _executor = None


def executor_max_workers() -> int:
    return _max_workers


def reset_executor_for_tests() -> None:
    """Test helper — shut down (wait for jobs) and allow a fresh pool."""
    shutdown_executor(wait=True)
