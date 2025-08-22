from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import os

LOGGER = logging.getLogger(__name__)
# Capture the DB path at import time for report calls to avoid cross-test reloads
try:
    from .paths import DB_PATH as REPORTS_DB_PATH
except Exception:  # pragma: no cover
    REPORTS_DB_PATH = None  # type: ignore


def _parse_iso_to_utc(dt_str: str) -> datetime:
    """Parse an ISO-like string into an aware UTC datetime.

    Rules:
    - If the string ends with 'Z', parse as UTC.
    - Otherwise, attempt datetime.fromisoformat(). If result is naive, attach UTC.
    - If result is aware, convert to UTC.
    Always returns an aware datetime in UTC.
    """
    # Fast path for Zulu
    if dt_str.endswith("Z"):
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    # Try stdlib parse
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_start_iso(date_or_iso: str) -> str:
    """Normalize input to UTC ISO with seconds precision, for inclusive start bound.

    - If input is YYYY-MM-DD, returns YYYY-MM-DDT00:00:00Z
    - If input has time, normalize to ...Z (UTC)
    """
    if len(date_or_iso) == 10 and date_or_iso.count("-") == 2:
        # Assume YYYY-MM-DD
        dt = datetime.strptime(date_or_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = _parse_iso_to_utc(date_or_iso)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_end_iso(date_or_iso: str) -> str:
    """Normalize input to UTC ISO for exclusive end bound.

    - If input is YYYY-MM-DD, interpret as start of that day (exclusive end at 00:00Z)
    - If input has time, normalize to ...Z (UTC) and return as-is (already exclusive)
    """
    if len(date_or_iso) == 10 and date_or_iso.count("-") == 2:
        dt = datetime.strptime(date_or_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = _parse_iso_to_utc(date_or_iso)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_midnight(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def daily_totals(employee_id: int, start_iso: str, end_iso: str) -> Dict[str, int]:
    # Normalize bounds: start inclusive, end exclusive
    s = to_utc_start_iso(start_iso)
    e = to_utc_end_iso(end_iso)

    LOGGER.debug("daily_totals bounds resolved: [%s, %s)", s, e)

    # Prepare buckets for each calendar day in [s,e)
    s_dt = _parse_iso_to_utc(s)
    e_dt = _parse_iso_to_utc(e)
    buckets: Dict[str, int] = {}
    day_cursor = _utc_midnight(s_dt)
    while day_cursor < e_dt:
        buckets[day_cursor.strftime("%Y-%m-%d")] = 0
        day_cursor += timedelta(days=1)

    # Fetch and clamp intervals once, then split across days
    # Import lazily to avoid stale references across test reloads
    from .repo import worked_intervals
    # Ensure repo uses the same DB path captured when reports was imported
    prev = os.environ.get("PUNCHPAD_REPORTS_DB_PATH")
    if REPORTS_DB_PATH:
        os.environ["PUNCHPAD_REPORTS_DB_PATH"] = str(REPORTS_DB_PATH)
    try:
        intervals_iter = worked_intervals(employee_id, s, e)
    finally:
        # Restore previous env to avoid leaking into other tests
        if prev is None:
            os.environ.pop("PUNCHPAD_REPORTS_DB_PATH", None)
        else:
            os.environ["PUNCHPAD_REPORTS_DB_PATH"] = prev

    for start_str, end_str in intervals_iter:
        start_dt = _parse_iso_to_utc(start_str)
        end_dt = _parse_iso_to_utc(end_str)
        if end_dt <= start_dt:
            continue

        cursor = start_dt
        while cursor < end_dt:
            # Next UTC midnight after cursor
            next_midnight = _utc_midnight(cursor) + timedelta(days=1)
            segment_end = min(end_dt, next_midnight)
            seconds = int(max(0, (segment_end - cursor).total_seconds()))
            if seconds > 0:
                key = _utc_midnight(cursor).strftime("%Y-%m-%d")
                # Ensure key exists even if outside initial range due to rounding
                if key not in buckets:
                    buckets[key] = 0
                buckets[key] += seconds
            cursor = segment_end

    # Ensure we only return days strictly before the exclusive end day
    end_day_midnight = _utc_midnight(e_dt)
    filtered: Dict[str, int] = {}
    for day_str, secs in buckets.items():
        day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if day_dt < end_day_midnight:
            filtered[day_str] = secs
    return filtered


def period_total(employee_id: int, start_iso: str, end_iso: str) -> int:
    s = to_utc_start_iso(start_iso)
    e = to_utc_end_iso(end_iso)
    LOGGER.debug("period_total bounds resolved: [%s, %s)", s, e)
    # Import lazily to avoid stale references across test reloads
    from .repo import total_seconds_worked
    prev = os.environ.get("PUNCHPAD_REPORTS_DB_PATH")
    if REPORTS_DB_PATH:
        os.environ["PUNCHPAD_REPORTS_DB_PATH"] = str(REPORTS_DB_PATH)
    try:
        return total_seconds_worked(employee_id, s, e)
    finally:
        if prev is None:
            os.environ.pop("PUNCHPAD_REPORTS_DB_PATH", None)
        else:
            os.environ["PUNCHPAD_REPORTS_DB_PATH"] = prev


def to_csv(rows: List[dict], filepath: str) -> None:
    if not rows:
        # Write header only if empty input? We'll include header with no rows.
        fieldnames = ["date", "employee_id", "seconds"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        return
    fieldnames = list(rows[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
