#!/usr/bin/env python3
from __future__ import annotations

import getpass
import logging
import re
import sys
from pathlib import Path

# Ensure project root is importable when running as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from punchpad_app.core.logging_setup import setup_logging
from punchpad_app.core.paths import DB_PATH
from punchpad_app.core.repo import set_setting
from punchpad_app.core.security import make_pin_hash
from punchpad_app.core.db import get_conn, apply_migrations, seed_default_settings


PIN_RE = re.compile(r"^\d{4,8}$")


def prompt_pin(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except Exception:
        print("Warning: Unable to hide input; PIN will be visible.")
        return input(prompt)


def main() -> int:
    setup_logging(dev_console=True)
    logging.getLogger(__name__).info("Manager bootstrap start; DB=%s", DB_PATH)

    # Ensure DB schema is ready and defaults exist
    with get_conn(DB_PATH) as conn:
        list(apply_migrations(conn))
        seed_default_settings(conn)

    pin1 = prompt_pin("Enter manager PIN (4-8 digits): ")
    pin2 = prompt_pin("Confirm manager PIN: ")

    if pin1 != pin2:
        print("Error: PINs do not match.")
        return 1
    if not PIN_RE.match(pin1):
        print("Error: PIN must be 4–8 digits.")
        return 1

    hashed = make_pin_hash(pin1)
    set_setting("manager_pin_hash", hashed)

    logging.getLogger(__name__).info("Manager PIN set successfully")
    print(f"Manager PIN set. DB: {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
