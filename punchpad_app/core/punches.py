from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
import sqlite3

from .queue import enqueue_event
from .repo import get_open_punch, insert_punch, close_open_punch
from .repo import append_audit

LOGGER = logging.getLogger(__name__)


def _utc_iso_now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clock_in(employee_id: int, method: str = "kiosk", note: str | None = None) -> dict:
    ts = _utc_iso_now_z()
    try:
        if get_open_punch(employee_id) is not None:
            raise ValueError("open punch exists")
        punch_id = insert_punch(employee_id, ts, method, note)
        LOGGER.info("clock_in success emp=%s punch_id=%s", employee_id, punch_id)
        return {"status": "ok", "queued": False, "punch_id": punch_id, "ts": ts}
    except Exception as e:
        ev = {
            "id": str(uuid.uuid4()),
            "kind": "clock_in",
            "employee_id": int(employee_id),
            "ts": ts,
            "note": note,
            "method": method,
        }
        enqueue_event(ev)
        LOGGER.info("clock_in queued emp=%s reason=%s", employee_id, type(e).__name__)
        return {"status": "queued", "queued": True, "event_id": ev["id"], "ts": ts}


def clock_out(employee_id: int, method: str = "kiosk", note: str | None = None) -> dict:
    ts = _utc_iso_now_z()
    try:
        punch_id = close_open_punch(employee_id, ts)
        LOGGER.info("clock_out success emp=%s punch_id=%s", employee_id, punch_id)
        return {"status": "ok", "queued": False, "punch_id": punch_id, "ts": ts}
    except Exception as e:
        ev = {
            "id": str(uuid.uuid4()),
            "kind": "clock_out",
            "employee_id": int(employee_id),
            "ts": ts,
            "note": note,
            "method": method,
        }
        enqueue_event(ev)
        LOGGER.info("clock_out queued emp=%s reason=%s", employee_id, type(e).__name__)
        return {"status": "queued", "queued": True, "event_id": ev["id"], "ts": ts}


def should_block_duplicate(
    conn: sqlite3.Connection,
    employee_id: int,
    action: str,  # "in" or "out"
    debounce_seconds: int,
    now_iso: str,
) -> bool:
    # Find most recent action and its timestamp
    row = conn.execute(
        "SELECT clock_in, clock_out FROM punches WHERE employee_id=? ORDER BY id DESC LIMIT 1",
        (employee_id,),
    ).fetchone()
    if not row:
        return False
    last_action = "in" if row["clock_out"] is None else "out"
    last_ts = row["clock_in"] if last_action == "in" else row["clock_out"]
    if not last_ts:
        return False
    try:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except Exception:
        return False
    delta = (now_dt - last_dt).total_seconds()
    if last_action == action and delta >= 0 and delta < debounce_seconds:
        LOGGER.info(
            "punch.blocked_duplicate employee_id=%s action=%s since_last=%s",
            employee_id,
            action,
            int(delta),
        )
        return True
    return False


def toggle_punch(
    conn: sqlite3.Connection,
    employee_id: int,
    method: str,
    note: str | None,
    now_iso: str,
) -> dict:
    # Determine desired action
    open_row = conn.execute(
        "SELECT id FROM punches WHERE employee_id=? AND clock_out IS NULL",
        (employee_id,),
    ).fetchone()
    action = "out" if open_row else "in"

    # Load debounce window from settings via separate import to avoid cycle
    from .repo import get_setting

    debounce_seconds = int(get_setting("kiosk.debounce_seconds") or 30)
    if should_block_duplicate(conn, employee_id, action, debounce_seconds, now_iso):
        # Caller should write audit with source info; we log here
        return {"status": "blocked", "reason": "duplicate", "action": action,
                "retry_after_seconds": max(0, debounce_seconds)}

    # Perform action using DB-first then queue fallback helpers
    if action == "in":
        res = clock_in(employee_id, method=method, note=note)
    else:
        res = clock_out(employee_id, method=method, note=note)
    res["action"] = action
    return res
