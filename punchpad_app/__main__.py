from __future__ import annotations

import logging
import sys

from .core import paths  # ensures dirs are created on import
from .core.logging_setup import setup_logging
from .core.db import get_conn, apply_migrations, seed_default_settings
from .core.reconciler import start_reconciler
from .core.config import get_config
from .core.db import get_conn as db_get_conn
from .core.security import verify_employee_pin, check_pin_lockout, record_pin_attempt
from .core.punches import toggle_punch
from .core.repo import append_audit
from .core.reports import daily_totals as rpt_daily_totals, period_total as rpt_period_total, to_csv as rpt_to_csv
import socket
import getpass
from datetime import datetime, timezone


def main() -> int:
    # Initialize logging (creates logs/app.log)
    setup_logging(dev_console=True)
    logger = logging.getLogger(__name__)

    logger.info("PunchPad start — data dir: %s", paths.DATA_DIR)
    logger.info("SQLite DB path: %s", paths.DB_PATH)

    # Open connection, apply migrations, seed defaults
    with get_conn(paths.DB_PATH) as conn:
        applied = list(apply_migrations(conn))
        if applied:
            logger.info("Applied migrations: %s", ", ".join(map(str, applied)))
        else:
            logger.info("No migrations to apply; already up to date")

        # Ensure default settings exist
        seed_default_settings(conn)

    logger.info("DB ready")
    # Start reconciler (background daemon)
    stop_event = start_reconciler()
    interval = int(get_config().get("jobs", {}).get("reconcile_interval_seconds", 5))
    logger.info("Reconciler started (interval=%ss)", interval)
    print(f"PunchPad DB ready — path: {paths.DB_PATH}")

    # Simple CLI: kiosk pin
    if len(sys.argv) >= 2 and sys.argv[1] == "kiosk" and (len(sys.argv) == 2 or sys.argv[2] == "pin"):
        source = socket.gethostname()
        note = None
        # Very small flag parser for --source and --note
        args = sys.argv[2:] if len(sys.argv) > 2 else []
        i = 0
        while i < len(args):
            if args[i] == "pin":
                i += 1
                continue
            if args[i] == "--source" and i + 1 < len(args):
                source = args[i + 1]
                i += 2
                continue
            if args[i] == "--note" and i + 1 < len(args):
                note = args[i + 1]
                i += 2
                continue
            i += 1

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pin = None
        try:
            pin = getpass.getpass("Enter PIN: ")
        except Exception:
            print("Warning: Unable to hide input; PIN may be visible.")
            pin = input("Enter PIN: ")

        with db_get_conn(paths.DB_PATH) as conn:
            locked, until_iso = check_pin_lockout(conn, source, now_iso)
            if locked:
                logging.getLogger(__name__).info("auth.pin_fail source=%s reason=locked", source)
                append_audit("system", "auth.lockout", "auth_lockout", None, {"source": source, "until": until_iso})
                print("Locked out due to too many attempts. Try later.")
                return 1

            emp_id = verify_employee_pin(conn, pin)
            if emp_id is None:
                # Record failed attempt
                record_pin_attempt(conn, source, now_iso, False, None, "bad_pin")
                # Count fails in window for logging
                logging.getLogger(__name__).info("auth.pin_fail source=%s reason=bad_pin", source)
                print("Invalid PIN.")
                return 1

            # Success
            record_pin_attempt(conn, source, now_iso, True, emp_id, None)
            logging.getLogger(__name__).info("auth.pin_ok source=%s employee_id=%s", source, emp_id)

        # Toggle punch using DB-first then queue fallback
        with db_get_conn(paths.DB_PATH) as conn:
            res = toggle_punch(conn, emp_id, method="kiosk", note=note, now_iso=now_iso)
            action = res.get("action")
            if res.get("status") == "blocked":
                append_audit("system", "punch.blocked", "punch_blocked", emp_id, {"action": action, "source": source, "reason": res.get("reason")})
                print("Duplicate punch blocked — try again later.")
                return 0
            elif res.get("status") in ("ok", "queued"):
                # Audit punch
                act = "punch.clock_in" if action == "in" else "punch.clock_out"
                append_audit("system", act, "punch", res.get("punch_id"), {"employee_id": emp_id, "via": "kiosk", "source": source})
                # Print friendly message
                hhmm = datetime.now(timezone.utc).strftime("%H:%M")
                if action == "in":
                    print(f"IN: PUNCHED IN ({hhmm}) — Have a great shift!")
                else:
                    print(f"OUT: PUNCHED OUT ({hhmm}) — See you next time!")
                return 0
            else:
                print("Unexpected result.")
                return 1

    # Reports CLI
    if len(sys.argv) >= 2 and sys.argv[1] == "report":
        # Parse subcommand
        if len(sys.argv) < 3:
            print("Usage: python -m punchpad_app report <daily|period> ...")
            return 1
        sub = sys.argv[2]
        # Parse common flags
        emp_id = None
        start = None
        end = None
        csv_path = None
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--emp" and i + 1 < len(args):
                emp_id = int(args[i + 1])
                i += 2
                continue
            if args[i] == "--start" and i + 1 < len(args):
                start = args[i + 1]
                i += 2
                continue
            if args[i] == "--end" and i + 1 < len(args):
                end = args[i + 1]
                i += 2
                continue
            if args[i] == "--csv" and i + 1 < len(args):
                csv_path = args[i + 1]
                i += 2
                continue
            i += 1
        if not emp_id or not start or not end:
            print("Missing required flags: --emp, --start, --end")
            return 1

        if sub == "daily":
            totals = rpt_daily_totals(emp_id, start, end)
            # Print in hours:min per day
            for day in sorted(totals.keys()):
                secs = int(totals[day])
                hours = secs // 3600
                minutes = (secs % 3600) // 60
                print(f"{day}: {hours:02d}:{minutes:02d}")
            if csv_path:
                rows = [{"date": d, "employee_id": emp_id, "seconds": int(totals[d])} for d in sorted(totals.keys())]
                rpt_to_csv(rows, csv_path)
        elif sub == "period":
            secs = rpt_period_total(emp_id, start, end)
            hours = secs // 3600
            minutes = (secs % 3600) // 60
            print(f"Total: {hours:02d}:{minutes:02d}")
        else:
            print("Unknown report subcommand; use daily or period")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
