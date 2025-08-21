from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .paths import CONFIG_PATH


DEFAULT_CONFIG: Dict[str, Any] = {
    "pay_period": "weekly",
    "week_start": "Monday",
    "rounding_minutes": 0,
    "overtime_policy": "none",
    "ui": {
        "idle_logout_seconds": 60,
        "keypad_mode": "auto",
        "keypad_hotplug_poll_seconds": 2,
        "theme": {
            "primary": "#1F3A5F",
            "accent": "#00C2A8",
            "bg": "#F5F7FA",
            "text": "#0F172A",
            "success": "#16A34A",
            "danger": "#DC2626",
            "font_family": "Segoe UI",
        },
    },
    "backups": {"keep_days": 90},
    "jobs": {"reconcile_interval_seconds": 5},
}


def _ensure_default_config(path: Path = CONFIG_PATH) -> None:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2))


def get_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    _ensure_default_config(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: Dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
