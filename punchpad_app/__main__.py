from __future__ import annotations

import sys

from .core import paths  # ensures dirs are created on import
from .core.config import get_config
from .core.logging_setup import setup_logging


def main() -> int:
    # Initialize logging (creates logs/app.log)
    setup_logging(dev_console=True)

    # Ensure default config exists and load it
    _ = get_config()

    print(f"PunchPad dev stub — data dir: {paths.DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
