from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Set

from .paths import QUEUE_PATH

LOGGER = logging.getLogger(__name__)


def enqueue_event(event: Dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":")) + "\n"
    # Open in append text mode with line buffering
    with open(QUEUE_PATH, "a", encoding="utf-8", buffering=1) as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    LOGGER.info("Enqueued event id=%s kind=%s emp=%s", event.get("id"), event.get("kind"), event.get("employee_id"))


def iter_events() -> Iterator[Dict]:
    path = QUEUE_PATH
    if not path.exists():
        return iter(())
    def _gen() -> Iterator[Dict]:
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict) or "id" not in obj:
                        LOGGER.warning("Queue: skipping invalid object at line %d", idx)
                        continue
                    yield obj
                except Exception:
                    LOGGER.warning("Queue: skipping corrupt line %d", idx)
                    continue
    return _gen()


def _fsync_directory(path: Path) -> None:
    try:
        dir_fd = os.open(str(path), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        # Best-effort; ignore on platforms without O_DIRECTORY
        pass


def remove_events(ids: List[str]) -> None:
    if not ids:
        return
    ids_set: Set[str] = set(ids)
    src = QUEUE_PATH
    if not src.exists():
        return

    tmp_fd, tmp_path_str = tempfile.mkstemp(prefix="punch_queue_", suffix=".ndjson.tmp", dir=str(src.parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as out_f, open(src, "r", encoding="utf-8") as in_f:
            kept = 0
            removed = 0
            for line in in_f:
                try:
                    obj = json.loads(line)
                    ev_id = obj.get("id") if isinstance(obj, dict) else None
                except Exception:
                    ev_id = None
                if ev_id and ev_id in ids_set:
                    removed += 1
                    continue
                out_f.write(line)
                kept += 1
            out_f.flush()
            os.fsync(out_f.fileno())
        # Atomic replace
        os.replace(tmp_path, src)
        _fsync_directory(src.parent)
        LOGGER.info("Queue compacted: removed=%d kept=%d", removed, kept)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
