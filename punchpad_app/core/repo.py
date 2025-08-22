from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .db import get_conn
from .paths import DB_PATH
# Avoid importing security at module import time to prevent cycles.
# Import inside functions that require it.

LOGGER = logging.getLogger(__name__)

# Utilities

def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Settings

def get_setting(key: str) -> Optional[str]:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None


def set_setting(key: str, val: str) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, val))
        LOGGER.info("Setting saved: %s", key)


# Employees

def add_employee(name: str, pay_rate: float, pin_plain: str) -> int:
    now = _utc_iso_now()
    # Local import to avoid circular dependency with security -> repo
    from .security import make_pin_hash
    pin_h = make_pin_hash(pin_plain)
    with get_conn(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO employees(name, pin_hash, pay_rate, active, created_at) VALUES(?, ?, ?, 1, ?)",
            (name, pin_h, float(pay_rate), now),
        )
        emp_id = cur.lastrowid
        append_audit("manager:bootstrap", "employee.add", "employee", emp_id, {"name": name})
        LOGGER.info("Employee added: id=%s, name=%s", emp_id, name)
        return int(emp_id)


def disable_employee(emp_id: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("UPDATE employees SET active=0 WHERE id=?", (emp_id,))
    append_audit("manager:bootstrap", "employee.disable", "employee", emp_id, None)
    LOGGER.info("Employee disabled: id=%s", emp_id)


def reset_employee_pin(emp_id: int, pin_plain: str) -> None:
    # Local import to avoid circular dependency
    from .security import make_pin_hash
    pin_h = make_pin_hash(pin_plain)
    with get_conn(DB_PATH) as conn:
        conn.execute("UPDATE employees SET pin_hash=? WHERE id=?", (pin_h, emp_id))
    append_audit("manager:bootstrap", "employee.reset_pin", "employee", emp_id, None)
    LOGGER.info("Employee PIN reset: id=%s", emp_id)


def get_employee(emp_id: int) -> Optional[sqlite3.Row]:
    with get_conn(DB_PATH) as conn:
        return conn.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()


def list_employees(active_only: bool = True) -> List[sqlite3.Row]:
    sql = "SELECT * FROM employees"
    params = ()
    if active_only:
        sql += " WHERE active=1"
    with get_conn(DB_PATH) as conn:
        return list(conn.execute(sql, params).fetchall())


def get_employee_by_pin(pin_plain: str) -> Optional[sqlite3.Row]:
    with get_conn(DB_PATH) as conn:
        # Only active employees
        # Local import to avoid circular dependency
        from .security import verify_pin
        for row in conn.execute("SELECT * FROM employees WHERE active=1"):
            if verify_pin(pin_plain, row["pin_hash"]):
                return row
        return None


# Audit

def append_audit(actor: str, action: str, target_type: str, target_id: int | None, meta: dict | None) -> None:
    now = _utc_iso_now()
    meta_json = json.dumps(meta) if meta is not None else None
    with get_conn(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO audit_log(actor, action, target_type, target_id, meta_json, created_at) VALUES(?,?,?,?,?,?)",
            (actor, action, target_type, target_id, meta_json, now),
        )
    LOGGER.info("Audit: %s %s %s id=%s", actor, action, target_type, target_id)


# Punches (low-level)

def get_open_punch(employee_id: int) -> Optional[sqlite3.Row]:
    with get_conn(DB_PATH) as conn:
        return conn.execute(
            "SELECT * FROM punches WHERE employee_id=? AND clock_out IS NULL",
            (employee_id,),
        ).fetchone()


def insert_punch(employee_id: int, clock_in_iso: str, method: str, note: str | None) -> int:
    with get_conn(DB_PATH) as conn:
        # Ensure no open punch exists
        row = conn.execute(
            "SELECT id FROM punches WHERE employee_id=? AND clock_out IS NULL",
            (employee_id,),
        ).fetchone()
        if row:
            raise sqlite3.IntegrityError("open punch already exists")
        cur = conn.execute(
            "INSERT INTO punches(employee_id, clock_in, clock_out, method, note) VALUES(?, ?, NULL, ?, ?)",
            (employee_id, clock_in_iso, method, note),
        )
        punch_id = int(cur.lastrowid)
        append_audit("system", "punch.clock_in", "punch", punch_id, {"employee_id": employee_id})
        LOGGER.info("Punch clock_in: id=%s emp=%s", punch_id, employee_id)
        return punch_id


def close_open_punch(employee_id: int, clock_out_iso: str) -> int:
    with get_conn(DB_PATH) as conn:
        rows = list(
            conn.execute(
                "SELECT id FROM punches WHERE employee_id=? AND clock_out IS NULL",
                (employee_id,),
            )
        )
        if len(rows) != 1:
            raise sqlite3.IntegrityError("expected exactly one open punch")
        punch_id = int(rows[0][0])
        conn.execute(
            "UPDATE punches SET clock_out=? WHERE id=?",
            (clock_out_iso, punch_id),
        )
        append_audit("system", "punch.clock_out", "punch", punch_id, {"employee_id": employee_id})
        LOGGER.info("Punch clock_out: id=%s emp=%s", punch_id, employee_id)
        return punch_id


# Reporting helpers

def list_punches_between(employee_id: int, start_iso: str, end_iso: str) -> List[sqlite3.Row]:
    """List closed punches overlapping [start, end).

    Start bound is inclusive, end bound is exclusive.
    Inputs are normalized to UTC Zulu strings before querying.
    """
    # Local import to avoid circular dependency with reports
    from .reports import to_utc_start_iso, to_utc_end_iso
    s = to_utc_start_iso(start_iso)
    e = to_utc_end_iso(end_iso)
    # Import connection helper and DB path at call-time to honor test reloads and env
    from .db import get_conn
    # Read override DB path if reports set one for this call; else use current module's DB_PATH
    import os as _os
    override = _os.environ.get("PUNCHPAD_REPORTS_DB_PATH")
    if override:
        db_path = override
    else:
        from .paths import DB_PATH as CURRENT_DB_PATH
        db_path = str(CURRENT_DB_PATH)
    with get_conn(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT id, employee_id, clock_in, clock_out, method, note
                FROM punches
                WHERE employee_id = ?
                  AND clock_in < ?
                  AND clock_out IS NOT NULL
                  AND clock_out > ?
                ORDER BY clock_in ASC
                """,
                (employee_id, e, s),
            ).fetchall()
        )


def worked_intervals(employee_id: int, start_iso: str, end_iso: str) -> List[Tuple[str, str]]:
    """Return clipped closed intervals within [start, end).

    Bounds are normalized to UTC Z strings, and each interval is clamped to [s, e).
    """
    # Local import to avoid circular dependency with reports
    from .reports import to_utc_start_iso, to_utc_end_iso
    s = to_utc_start_iso(start_iso)
    e = to_utc_end_iso(end_iso)
    rows = list_punches_between(employee_id, s, e)
    intervals: List[Tuple[str, str]] = []
    for r in rows:
        ci = r["clock_in"]
        co = r["clock_out"]
        if not co:
            continue
        start = max(ci, s)
        end = min(co, e)
        if end > start:
            intervals.append((start, end))
    return intervals


def total_seconds_worked(employee_id: int, start_iso: str, end_iso: str) -> int:
    from datetime import datetime
    # Local import to avoid circular dependency with reports
    from .reports import to_utc_start_iso, to_utc_end_iso
    s = to_utc_start_iso(start_iso)
    e = to_utc_end_iso(end_iso)
    total = 0
    for s_iso, e_iso in worked_intervals(employee_id, s, e):
        sd = datetime.fromisoformat(s_iso.replace("Z", "+00:00"))
        ed = datetime.fromisoformat(e_iso.replace("Z", "+00:00"))
        seconds = int(max(0, (ed - sd).total_seconds()))
        if seconds > 0:
            total += seconds
    return int(total)
