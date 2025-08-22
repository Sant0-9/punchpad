from __future__ import annotations

import logging
import threading
import time
from typing import List

from .queue import iter_events, remove_events
from .repo import insert_punch, close_open_punch
from .config import get_config

LOGGER = logging.getLogger(__name__)


def _apply_event(ev: dict) -> bool:
    kind = ev.get("kind")
    emp_id = ev.get("employee_id")
    ts = ev.get("ts")
    method = ev.get("method")
    note = ev.get("note")
    if kind == "clock_in":
        insert_punch(int(emp_id), ts, method, note)
        return True
    elif kind == "clock_out":
        close_open_punch(int(emp_id), ts)
        return True
    else:
        LOGGER.warning("Reconciler: unknown event kind=%s", kind)
        return True  # drop unknown to avoid blocking


def _run_loop(stop_event: threading.Event) -> None:
    cfg = get_config()
    interval = int(cfg.get("jobs", {}).get("reconcile_interval_seconds", 5))
    LOGGER.info("Reconciler started (interval=%ss)", interval)
    while not stop_event.is_set():
        applied_ids: List[str] = []
        try:
            for ev in list(iter_events()):
                ev_id = ev.get("id")
                try:
                    if _apply_event(ev):
                        applied_ids.append(ev_id)
                except Exception as e:
                    LOGGER.warning("Reconciler: DB apply failed for event %s: %s", ev_id, e)
                    # Leave event for next tick
            if applied_ids:
                remove_events(applied_ids)
        except Exception as e:
            LOGGER.warning("Reconciler tick error: %s", e)
        stop_event.wait(interval)


def start_reconciler() -> threading.Event:
    stop_event = threading.Event()
    t = threading.Thread(target=_run_loop, args=(stop_event,), name="punchpad-reconciler", daemon=True)
    t.start()
    return stop_event
