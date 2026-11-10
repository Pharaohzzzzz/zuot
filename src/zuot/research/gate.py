"""Read the newest research report and answer whether the Gate passed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import AppConfig


def latest_report(cfg: AppConfig) -> tuple[Path | None, dict[str, Any] | None]:
    """Return the most recently written research report, if any."""
    if not cfg.report_dir.exists():
        return None, None
    paths = sorted(
        cfg.report_dir.glob("research_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return path, payload
    return None, None


def gate_status(cfg: AppConfig) -> tuple[bool, str]:
    """Live trading is allowed only when the newest report lists a passing strategy."""
    path, payload = latest_report(cfg)
    if path is None or payload is None:
        return False, "no research report found; run `zuot research` first"
    passed = payload.get("passed") or []
    if passed:
        return True, f"{path.name}: passed {', '.join(str(name) for name in passed)}"
    generated = payload.get("generated_at", "unknown time")
    return False, f"{path.name} ({generated}): no strategy passed the Gate"
