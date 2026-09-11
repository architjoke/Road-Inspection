"""Model version metadata helper."""

from pathlib import Path


def current_model_version(model_path: str | None = None) -> str:
    if model_path:
        return Path(model_path).name
    return "best.pt"
