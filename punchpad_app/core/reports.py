from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .repo import worked_intervals, total_seconds_worked


def _day_bounds(day_iso: str) -> tuple[str, str]:
    start = f"{day_iso}T00:00:00Z"
    end_dt = datetime.strptime(day_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, end


def daily_totals(employee_id: int, start_iso: str, end_iso: str) -> Dict[str, int]:
    # Build per-day buckets between start_date (inclusive) and end_date (exclusive)
    start_dt = datetime.strptime(start_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days: Dict[str, int] = {}
    day = start_dt
    while day < end_dt:
        day_str = day.strftime("%Y-%m-%d")
        d_start, d_end = _day_bounds(day_str)
        secs = total_seconds_worked(employee_id, d_start, d_end)
        if secs:
            days[day_str] = secs
        else:
            days.setdefault(day_str, 0)
        day += timedelta(days=1)
    return days


def period_total(employee_id: int, start_iso: str, end_iso: str) -> int:
    # start_iso and end_iso as YYYY-MM-DD
    start = f"{start_iso}T00:00:00Z"
    end = f"{end_iso}T00:00:00Z"
    return total_seconds_worked(employee_id, start, end)


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
