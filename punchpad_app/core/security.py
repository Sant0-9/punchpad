from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional

from .repo import get_setting


_SCHEME = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 200_000  # >=150k per spec
_SALT_BYTES = 16


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def make_pin_hash(pin: str) -> str:
    if not isinstance(pin, str):
        raise TypeError("pin must be a string")
    salt = os.urandom(_SALT_BYTES)
    iterations = _DEFAULT_ITERATIONS
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return f"{_SCHEME}${iterations}${_b64e(salt)}${_b64e(dk)}"


def _parse_stored(stored: str) -> Tuple[int, bytes, bytes]:
    parts = stored.split("$")
    if len(parts) != 4:
        raise ValueError("invalid format")
    scheme, iter_s, salt_b64, hash_b64 = parts
    if scheme != _SCHEME:
        raise ValueError("unsupported scheme")
    iterations = int(iter_s)
    if iterations < 150_000:
        # Treat as invalid even if lower iteration hashes exist
        raise ValueError("iterations too low")
    salt = _b64d(salt_b64)
    digest = _b64d(hash_b64)
    if len(salt) != _SALT_BYTES:
        raise ValueError("invalid salt length")
    return iterations, salt, digest


def verify_pin(pin: str, stored: str) -> bool:
    try:
        iterations, salt, expected = _parse_stored(stored)
        computed = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(computed, expected)
    except Exception:
        return False


# PIN verification against employees table (no PIN logging)
def verify_employee_pin(conn: sqlite3.Connection, pin: str) -> Optional[int]:
    for row in conn.execute("SELECT id, pin_hash FROM employees WHERE active=1"):
        if verify_pin(pin, row["pin_hash"]):
            return int(row["id"])
    return None


def _utc_iso_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_pin_lockout(conn: sqlite3.Connection, source: str, now_iso: str) -> tuple[bool, Optional[str]]:
    # Load settings
    window_s = int(get_setting("kiosk.pin_attempt_window_seconds") or 300)
    max_attempts = int(get_setting("kiosk.pin_max_attempts_per_window") or 5)
    lockout_min = int(get_setting("kiosk.lockout_minutes") or 10)

    # Compute window start
    now_dt = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    window_start = (now_dt - timedelta(seconds=window_s)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = list(
        conn.execute(
            "SELECT ts FROM pin_attempts WHERE source=? AND success=0 AND ts>=? ORDER BY ts DESC",
            (source, window_start),
        )
    )
    if len(rows) >= max_attempts:
        most_recent = rows[0][0]
        most_recent_dt = datetime.strptime(most_recent, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        locked_until = (most_recent_dt + timedelta(minutes=lockout_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if now_dt < most_recent_dt + timedelta(minutes=lockout_min):
            return True, locked_until
    return False, None


def record_pin_attempt(
    conn: sqlite3.Connection,
    source: str,
    now_iso: str,
    success: bool,
    employee_id: Optional[int],
    reason: Optional[str],
) -> None:
    conn.execute(
        "INSERT INTO pin_attempts(ts, source, success, employee_id, reason) VALUES(?,?,?,?,?)",
        (now_iso, source, 1 if success else 0, employee_id, reason),
    )
