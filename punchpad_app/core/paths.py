from __future__ import annotations

import os
import platform
from pathlib import Path

APP_DIR_NAME = "PunchPad"


def _default_data_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("ProgramData", r"C:\\ProgramData")) / APP_DIR_NAME
    env_override = os.environ.get("PUNCHPAD_DATA_DIR")
    if env_override:
        return Path(env_override)
    return Path.home() / ".local" / "share" / "punchpad"


DATA_DIR: Path = _default_data_dir()
DB_PATH: Path = DATA_DIR / "punchpad.sqlite"
BACKUPS_DIR: Path = DATA_DIR / "backups"
LOGS_DIR: Path = DATA_DIR / "logs"
REPORTS_DIR: Path = DATA_DIR / "reports"
CONFIG_PATH: Path = DATA_DIR / "config.json"
QUEUE_PATH: Path = DATA_DIR / "punch_queue.ndjson"

# Ensure base and required subdirectories exist
for directory in (DATA_DIR, BACKUPS_DIR, LOGS_DIR, REPORTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
