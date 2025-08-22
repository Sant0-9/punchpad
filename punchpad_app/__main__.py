from __future__ import annotations

import argparse
import logging
import sys

from .__version__ import __version__
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
from .tui.kiosk_screen import render_banner, _clear_screen, _sleep_ms, prompt_pin
from .web.server import run_server as web_run_server
import socket
import getpass
from datetime import datetime, timezone


def main() -> int:
    # Initialize logging (creates logs/app.log)
    setup_logging(dev_console=True)
    logger = logging.getLogger(__name__)

    # argparse CLI
    parser = argparse.ArgumentParser(prog="punchpad", description="PunchPad — kiosk PIN and reports")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    # kiosk group
    kiosk_parser = subparsers.add_parser("kiosk", help="Kiosk operations (PIN, run loop, web UI)")
    kiosk_sub = kiosk_parser.add_subparsers(dest="kiosk_cmd", metavar="subcommand")

    # kiosk pin
    kiosk_pin = kiosk_sub.add_parser("pin", help="Prompt for a PIN and toggle punch once")
    kiosk_pin.add_argument("--source", default=socket.gethostname(), help="Source identifier for audit/lockout")
    kiosk_pin.add_argument("--note", default=None, help="Optional note for the punch")

    # kiosk run
    kiosk_run = kiosk_sub.add_parser("run", help="Fullscreen kiosk loop for PIN entry")
    kiosk_run.add_argument("--source", default=socket.gethostname(), help="Source identifier for audit/lockout")
    kiosk_run.add_argument("--pin", default=None, help="Test mode: PIN to auto-enter and exit once")
    kiosk_run.add_argument("--result_ms", type=int, default=1800, help="Milliseconds to show result banner")

    # kiosk web
    kiosk_web = kiosk_sub.add_parser("web", help="Start local web kiosk UI")
    kiosk_web.add_argument("--host", default="127.0.0.1", help="Bind host")
    kiosk_web.add_argument("--port", type=int, default=8765, help="Bind port")
    kiosk_web.add_argument("--redirect-seconds", type=int, default=2, help="Seconds before redirect to PIN screen")
    kiosk_web.add_argument("--source", default=socket.gethostname(), help="Source identifier for audit/lockout")

    # report group
    report_parser = subparsers.add_parser("report", help="Reporting commands (daily totals, period total)")
    report_sub = report_parser.add_subparsers(dest="report_cmd", metavar="subcommand")

    report_daily = report_sub.add_parser("daily", help="Show daily totals for an employee between dates")
    report_daily.add_argument("--emp", type=int, required=True, help="Employee ID")
    report_daily.add_argument("--start", required=True, help="Start date or ISO (inclusive)")
    report_daily.add_argument("--end", required=True, help="End date or ISO (exclusive)")
    report_daily.add_argument("--csv", default=None, help="Optional CSV output path")

    report_period = report_sub.add_parser("period", help="Show total time for an employee between dates")
    report_period.add_argument("--emp", type=int, required=True, help="Employee ID")
    report_period.add_argument("--start", required=True, help="Start date or ISO (inclusive)")
    report_period.add_argument("--end", required=True, help="End date or ISO (exclusive)")

    args = parser.parse_args()

    # If no command, show help
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    # Handlers
    if args.command == "kiosk":
        # Ensure DB is ready for kiosk operations
        logger.info("PunchPad start — data dir: %s", paths.DATA_DIR)
        logger.info("SQLite DB path: %s", paths.DB_PATH)
        with get_conn(paths.DB_PATH) as conn:
            applied = list(apply_migrations(conn))
            if applied:
                logger.info("Applied migrations: %s", ", ".join(map(str, applied)))
            seed_default_settings(conn)
        if args.kiosk_cmd == "pin" or args.kiosk_cmd is None:
            source = args.source
            note = args.note
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                    record_pin_attempt(conn, source, now_iso, False, None, "bad_pin")
                    logging.getLogger(__name__).info("auth.pin_fail source=%s reason=bad_pin", source)
                    print("Invalid PIN.")
                    return 1
                record_pin_attempt(conn, source, now_iso, True, emp_id, None)

            with db_get_conn(paths.DB_PATH) as conn:
                res = toggle_punch(conn, emp_id, method="kiosk", note=note, now_iso=now_iso)
                action = res.get("action")
                if res.get("status") == "blocked":
                    append_audit("system", "punch.blocked", "punch_blocked", emp_id, {"action": action, "source": source, "reason": res.get("reason")})
                    print("Duplicate punch blocked — try again later.")
                    return 0
                elif res.get("status") in ("ok", "queued"):
                    act = "punch.clock_in" if action == "in" else "punch.clock_out"
                    append_audit("system", act, "punch", res.get("punch_id"), {"employee_id": emp_id, "via": "kiosk", "source": source})
                    hhmm = datetime.now(timezone.utc).strftime("%H:%M")
                    if action == "in":
                        print(f"IN: PUNCHED IN ({hhmm}) — Have a great shift!")
                    else:
                        print(f"OUT: PUNCHED OUT ({hhmm}) — See you next time!")
                    return 0
                else:
                    print("Unexpected result.")
                    return 1

        if args.kiosk_cmd == "run":
            source = args.source
            test_pin = args.pin
            result_ms = int(args.result_ms)

            logging.getLogger(__name__).info("kiosk.run start source=%s", source)

            def _getch() -> str:
                try:
                    import termios, tty  # type: ignore
                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)
                    try:
                        tty.setraw(fd)
                        ch = sys.stdin.read(1)
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    return ch
                except Exception:
                    return sys.stdin.read(1)

            while True:
                try:
                    _clear_screen()
                    print("PunchPad — Enter PIN")
                    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    with db_get_conn(paths.DB_PATH) as conn:
                        locked, until_iso = check_pin_lockout(conn, source, now_iso)
                    if locked:
                        banner = render_banner("locked", "Too many attempts", None)
                        print(banner, end="")
                        logging.getLogger(__name__).info("kiosk.result status=locked employee_id=- reason=lockout")
                        _sleep_ms(result_ms)
                        if test_pin is not None:
                            return 0
                        continue

                    if test_pin is not None:
                        pin_val = test_pin
                    else:
                        try:
                            pin_val = prompt_pin(_getch, echo=False)
                        except KeyboardInterrupt:
                            print("\nExiting kiosk.")
                            return 0

                    logging.getLogger(__name__).info("kiosk.pin received source=%s len=%s", source, len(pin_val) if pin_val is not None else 0)

                    with db_get_conn(paths.DB_PATH) as conn:
                        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        locked, until_iso = check_pin_lockout(conn, source, now_iso)
                        if locked:
                            banner = render_banner("locked", "Too many attempts", None)
                            print(banner, end="")
                            logging.getLogger(__name__).info("kiosk.result status=locked employee_id=- reason=lockout")
                            _sleep_ms(result_ms)
                            if test_pin is not None:
                                return 0
                            continue

                        emp_id = verify_employee_pin(conn, pin_val)
                        if emp_id is None:
                            record_pin_attempt(conn, source, now_iso, False, None, "bad_pin")
                            append_audit("system", "auth.pin_fail", "auth", None, {"source": source})
                            banner = render_banner("blocked", "Invalid PIN", None)
                            print(banner, end="")
                            logging.getLogger(__name__).info("kiosk.result status=blocked employee_id=- reason=bad_pin")
                            _sleep_ms(result_ms)
                            if test_pin is not None:
                                return 0
                            continue

                        record_pin_attempt(conn, source, now_iso, True, emp_id, None)

                    with db_get_conn(paths.DB_PATH) as conn:
                        res = toggle_punch(conn, emp_id, method="kiosk", note=None, now_iso=now_iso)
                        action = res.get("action")
                        status = res.get("status")
                        local_time = datetime.now().strftime("%H:%M")
                        if status == "blocked":
                            retry = res.get("retry_after_seconds")
                            banner = render_banner("blocked", "Try again soon", f"~{retry}s")
                            print(banner, end="")
                            logging.getLogger(__name__).info("kiosk.result status=blocked employee_id=%s reason=duplicate", emp_id)
                        else:
                            queued = status == "queued"
                            if action == "in":
                                msg = f"Clocked IN {local_time}" + (" (queued)" if queued else "")
                                banner = render_banner("ok_in", msg, None)
                                print(banner, end="")
                                logging.getLogger(__name__).info("kiosk.result status=%s employee_id=%s reason=-", "queued" if queued else "ok_in", emp_id)
                            else:
                                msg = f"Clocked OUT {local_time}" + (" (queued)" if queued else "")
                                banner = render_banner("ok_out", msg, None)
                                print(banner, end="")
                                logging.getLogger(__name__).info("kiosk.result status=%s employee_id=%s reason=-", "queued" if queued else "ok_out", emp_id)

                    _sleep_ms(result_ms)
                    if test_pin is not None:
                        return 0
                except KeyboardInterrupt:
                    print("\nExiting kiosk.")
                    return 0

        if args.kiosk_cmd == "web":
            host = args.host
            port = int(args.port)
            redirect_seconds = int(args.redirect_seconds)
            source = args.source
            logger.info("kiosk.web start host=%s port=%s", host, port)
            print(f"Starting PunchPad web on http://{host}:{port}/  (Reports at /reports)")
            with get_conn(paths.DB_PATH) as conn:
                list(apply_migrations(conn))
                seed_default_settings(conn)
            start_reconciler()
            try:
                web_run_server(host=host, port=port, redirect_seconds=redirect_seconds, source=source)
            except KeyboardInterrupt:
                print("\nStopping web server.")
            return 0

    if args.command == "report":
        # Ensure DB ready for reading reports
        logger.info("PunchPad start — data dir: %s", paths.DATA_DIR)
        logger.info("SQLite DB path: %s", paths.DB_PATH)
        with get_conn(paths.DB_PATH) as conn:
            list(apply_migrations(conn))
            seed_default_settings(conn)
        if args.report_cmd == "daily":
            emp_id = int(args.emp)
            start = args.start
            end = args.end
            csv_path = args.csv
            totals = rpt_daily_totals(emp_id, start, end)
            for day in sorted(totals.keys()):
                secs = int(totals[day])
                hours = secs // 3600
                minutes = (secs % 3600) // 60
                print(f"{day}: {hours:02d}:{minutes:02d}")
            if csv_path:
                rows = [{"date": d, "employee_id": emp_id, "seconds": int(totals[d])} for d in sorted(totals.keys())]
                rpt_to_csv(rows, csv_path)
            return 0
        if args.report_cmd == "period":
            emp_id = int(args.emp)
            start = args.start
            end = args.end
            secs = rpt_period_total(emp_id, start, end)
            hours = secs // 3600
            minutes = (secs % 3600) // 60
            print(f"Total: {hours:02d}:{minutes:02d}")
            return 0
        # If no subcommand, show help
        report_parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
