from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..core.db import get_conn, apply_migrations, seed_default_settings
from ..core.paths import DB_PATH
from ..core import security as _security
from ..core import repo as _repo
from ..core import reports as _reports
from ..core import punches as _punches


LOGGER = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_hhmm() -> str:
    return datetime.now().astimezone().strftime("%H:%M")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render_index() -> bytes:
    html_txt = _read_text(TEMPLATES_DIR / "index.html")
    return html_txt.encode("utf-8")


def _render_result(status: str, message: str, redirect_seconds: int) -> bytes:
    # status: ok_in | ok_out | blocked | locked | error
    template = _read_text(TEMPLATES_DIR / "result.html")
    status_class = {
        "ok_in": "banner banner-ok-in",
        "ok_out": "banner banner-ok-out",
        "blocked": "banner banner-blocked",
        "locked": "banner banner-locked",
        "error": "banner banner-error",
    }.get(status, "banner banner-error")
    safe_msg = html.escape(message, quote=False)
    page = (
        template
        .replace("{{status_class}}", status_class)
        .replace("{{message}}", safe_msg)
        .replace("{{redirect_seconds}}", str(int(max(0, redirect_seconds))))
    )
    return page.encode("utf-8")


class _KioskWebServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, *, redirect_seconds: int, source: str):
        super().__init__(server_address, RequestHandlerClass)
        self.redirect_seconds = int(redirect_seconds)
        self.source = source


class KioskRequestHandler(BaseHTTPRequestHandler):
    server: _KioskWebServer  # type: ignore[assignment]

    def _send_bytes(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_bytes(HTTPStatus.OK, _render_index())
                return
            if parsed.path == "/admin":
                # Render admin page with employees listing
                page = _read_text(TEMPLATES_DIR / "admin.html")
                # Load all employees
                try:
                    emps = _repo.list_employees(active_only=False)
                except Exception:
                    emps = []
                # Build rows
                def _last_punch(emp_id: int) -> str:
                    try:
                        with get_conn(DB_PATH) as conn:
                            row_open = conn.execute(
                                "SELECT clock_in FROM punches WHERE employee_id=? AND clock_out IS NULL ORDER BY clock_in DESC LIMIT 1",
                                (emp_id,),
                            ).fetchone()
                            if row_open:
                                return f"IN {html.escape(str(row_open[0]))}"
                            row = conn.execute(
                                "SELECT clock_out FROM punches WHERE employee_id=? AND clock_out IS NOT NULL ORDER BY clock_out DESC LIMIT 1",
                                (emp_id,),
                            ).fetchone()
                            if row:
                                return f"OUT {html.escape(str(row[0]))}"
                    except Exception:
                        pass
                    return "—"

                rows_html = "".join(
                    (
                        f"<tr>"
                        f"<td>{int(e['id'])}</td>"
                        f"<td>{html.escape(str(e['name']))}</td>"
                        f"<td>{'Yes' if int(e['active']) else 'No'}</td>"
                        f"<td>{_last_punch(int(e['id']))}</td>"
                        f"<td>"
                        f"<form method=\"post\" action=\"/admin/employee/toggle\" style=\"display:inline\">"
                        f"<input type=\"hidden\" name=\"employee_id\" value=\"{int(e['id'])}\" />"
                        f"<input type=\"hidden\" name=\"active\" value=\"{0 if int(e['active']) else 1}\" />"
                        f"<input class=\"pin-input small\" type=\"password\" name=\"manager_pin\" placeholder=\"Manager PIN\" />"
                        f"<button class=\"btn-small\" type=\"submit\">{'Disable' if int(e['active']) else 'Enable'}</button>"
                        f"</form>"
                        f"<form method=\"post\" action=\"/admin/employee/set_pin\" style=\"display:inline; margin-left:6px\">"
                        f"<input type=\"hidden\" name=\"employee_id\" value=\"{int(e['id'])}\" />"
                        f"<input class=\"pin-input small\" type=\"password\" name=\"new_pin\" placeholder=\"New PIN\" />"
                        f"<input class=\"pin-input small\" type=\"password\" name=\"manager_pin\" placeholder=\"Manager PIN\" />"
                        f"<button class=\"btn-small\" type=\"submit\">Set/Reset PIN</button>"
                        f"</form>"
                        f"<form method=\"post\" action=\"/admin/employee/punches\" style=\"display:inline; margin-left:6px\">"
                        f"<input type=\"hidden\" name=\"employee_id\" value=\"{int(e['id'])}\" />"
                        f"<button class=\"btn-small\" type=\"submit\">View Punches</button>"
                        f"</form>"
                        f"</td>"
                        f"</tr>"
                    )
                    for e in emps
                )
                page = (
                    page
                    .replace("{{success_html}}", "")
                    .replace("{{error_html}}", "")
                    .replace("{{employees_rows}}", rows_html)
                    .replace("{{punches_html}}", "")
                )
                self._send_bytes(HTTPStatus.OK, page.encode("utf-8"))
                return
            if parsed.path == "/reports":
                page = _read_text(TEMPLATES_DIR / "reports.html")
                page = (
                    page
                    .replace("{{employee_id}}", "")
                    .replace("{{start}}", "")
                    .replace("{{end}}", "")
                    .replace("{{error_html}}", "")
                    .replace("{{summary_html}}", "")
                    .replace("{{table_html}}", "")
                )
                self._send_bytes(HTTPStatus.OK, page.encode("utf-8"))
                return
            if parsed.path.startswith("/static/"):
                name = parsed.path.split("/static/", 1)[1]
                if name == "style.css":
                    path = STATIC_DIR / name
                    if path.exists():
                        self._send_bytes(HTTPStatus.OK, _read_text(path).encode("utf-8"), "text/css; charset=utf-8")
                        return
                self._send_bytes(HTTPStatus.NOT_FOUND, b"Not Found\n", "text/plain; charset=utf-8")
                return
            self._send_bytes(HTTPStatus.NOT_FOUND, b"Not Found\n", "text/plain; charset=utf-8")
        except Exception:
            LOGGER.exception("GET failed")
            self._send_bytes(HTTPStatus.INTERNAL_SERVER_ERROR, _render_result("error", "Something went wrong", self.server.redirect_seconds))

    def do_POST(self) -> None:  # noqa: N802
        try:
            # Defensive: ensure core modules see the current data dir for this process
            # Tests may import modules with different envs; reload to sync DB_PATH here.
            try:
                import importlib
                from ..core import paths as _paths_mod
                importlib.reload(_paths_mod)
                from ..core import repo as _repo_mod
                importlib.reload(_repo_mod)
                from ..core import security as _sec_mod
                importlib.reload(_sec_mod)
                sec = _sec_mod
            except Exception:
                sec = _security
            # Resolve DB path dynamically after reload
            try:
                db_path = _paths_mod.DB_PATH  # type: ignore[name-defined]
            except Exception:
                db_path = DB_PATH
            parsed = urlparse(self.path)
            if parsed.path == "/pin":
                # Existing PIN flow
                pass
            elif parsed.path in ("/reports/view", "/reports/csv"):
                # handled below
                pass
            elif parsed.path.startswith("/admin/"):
                # Admin actions handled below
                pass
            else:
                self._send_bytes(HTTPStatus.NOT_FOUND, b"Not Found\n", "text/plain; charset=utf-8")
                return

            # Read form body (application/x-www-form-urlencoded)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
            except Exception:
                raw = b""
            form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)

            if parsed.path == "/pin":
                pin = (form.get("pin") or [""])[0]
                source = (form.get("source") or [self.server.source])[0] or self.server.source
                now_iso = _utc_now_iso_z()
                LOGGER.info("kiosk.web pin received source=%s len=%s", source, len(pin))
                with get_conn(db_path) as conn:
                    list(apply_migrations(conn))
                    seed_default_settings(conn)
                with get_conn(db_path) as conn:
                    locked, _until = sec.check_pin_lockout(conn, source, now_iso)
                if locked:
                    body = _render_result("locked", "Locked — too many bad attempts", self.server.redirect_seconds)
                    self._send_bytes(HTTPStatus.OK, body)
                    LOGGER.info("kiosk.web result status=locked employee_id=- reason=lockout")
                    return
                with get_conn(db_path) as conn:
                    emp_id: Optional[int] = sec.verify_employee_pin(conn, pin)
                    if emp_id is None:
                        sec.record_pin_attempt(conn, source, now_iso, False, None, "bad_pin")
                        body = _render_result("blocked", "Invalid PIN", self.server.redirect_seconds)
                        self._send_bytes(HTTPStatus.OK, body)
                        LOGGER.info("kiosk.web result status=blocked employee_id=- reason=bad_pin")
                        return
                with get_conn(db_path) as conn:
                    sec.record_pin_attempt(conn, source, now_iso, True, emp_id, None)
                with get_conn(db_path) as conn:
                    res = _punches.toggle_punch(conn, int(emp_id), method="kiosk", note=None, now_iso=now_iso)
                action = res.get("action")
                status = res.get("status")
                queued = status == "queued"
                local_time = _local_hhmm()
                if status == "blocked":
                    retry = res.get("retry_after_seconds")
                    msg = f"Duplicate punch blocked — try again in ~{int(retry)}s" if retry is not None else "Duplicate punch blocked"
                    body = _render_result("blocked", msg, self.server.redirect_seconds)
                    self._send_bytes(HTTPStatus.OK, body)
                    LOGGER.info("kiosk.web result status=blocked employee_id=%s reason=duplicate", emp_id)
                    return
                if action == "in":
                    msg = f"PUNCHED IN — {local_time}" + (" (queued)" if queued else "")
                    body = _render_result("ok_in", msg, self.server.redirect_seconds)
                    self._send_bytes(HTTPStatus.OK, body)
                    LOGGER.info("kiosk.web result status=%s employee_id=%s reason=-", "queued" if queued else "ok_in", emp_id)
                else:
                    msg = f"PUNCHED OUT — {local_time}" + (" (queued)" if queued else "")
                    body = _render_result("ok_out", msg, self.server.redirect_seconds)
                    self._send_bytes(HTTPStatus.OK, body)
                    LOGGER.info("kiosk.web result status=%s employee_id=%s reason=-", "queued" if queued else "ok_out", emp_id)
                return

            # Admin handlers
            if parsed.path.startswith("/admin/"):
                def _render_admin(success_msg: str = "", error_msg: str = "", punches_html: str = "") -> None:
                    page = _read_text(TEMPLATES_DIR / "admin.html")
                    try:
                        emps = _repo.list_employees(active_only=False)
                    except Exception:
                        emps = []
                    def _last_punch(emp_id: int) -> str:
                        try:
                            with get_conn(db_path) as conn:
                                row_open = conn.execute(
                                    "SELECT clock_in FROM punches WHERE employee_id=? AND clock_out IS NULL ORDER BY clock_in DESC LIMIT 1",
                                    (emp_id,),
                                ).fetchone()
                                if row_open:
                                    return f"IN {html.escape(str(row_open[0]))}"
                                row = conn.execute(
                                    "SELECT clock_out FROM punches WHERE employee_id=? AND clock_out IS NOT NULL ORDER BY clock_out DESC LIMIT 1",
                                    (emp_id,),
                                ).fetchone()
                                if row:
                                    return f"OUT {html.escape(str(row[0]))}"
                        except Exception:
                            pass
                        return "—"
                    rows_html = "".join(
                        (
                            f"<tr>"
                            f"<td>{int(e['id'])}</td>"
                            f"<td>{html.escape(str(e['name']))}</td>"
                            f"<td>{'Yes' if int(e['active']) else 'No'}</td>"
                            f"<td>{_last_punch(int(e['id']))}</td>"
                            f"<td>"
                            f"<form method=\"post\" action=\"/admin/employee/toggle\" style=\"display:inline\">"
                            f"<input type=\"hidden\" name=\"employee_id\" value=\"{int(e['id'])}\" />"
                            f"<input type=\"hidden\" name=\"active\" value=\"{0 if int(e['active']) else 1}\" />"
                            f"<input class=\"pin-input small\" type=\"password\" name=\"manager_pin\" placeholder=\"Manager PIN\" />"
                            f"<button class=\"btn-small\" type=\"submit\">{'Disable' if int(e['active']) else 'Enable'}</button>"
                            f"</form>"
                            f"<form method=\"post\" action=\"/admin/employee/set_pin\" style=\"display:inline; margin-left:6px\">"
                            f"<input type=\"hidden\" name=\"employee_id\" value=\"{int(e['id'])}\" />"
                            f"<input class=\"pin-input small\" type=\"password\" name=\"new_pin\" placeholder=\"New PIN\" />"
                            f"<input class=\"pin-input small\" type=\"password\" name=\"manager_pin\" placeholder=\"Manager PIN\" />"
                            f"<button class=\"btn-small\" type=\"submit\">Set/Reset PIN</button>"
                            f"</form>"
                            f"<form method=\"post\" action=\"/admin/employee/punches\" style=\"display:inline; margin-left:6px\">"
                            f"<input type=\"hidden\" name=\"employee_id\" value=\"{int(e['id'])}\" />"
                            f"<button class=\"btn-small\" type=\"submit\">View Punches</button>"
                            f"</form>"
                            f"</td>"
                            f"</tr>"
                        )
                        for e in emps
                    )
                    page = (
                        page
                        .replace("{{success_html}}", f"<div class=\"banner success\">{html.escape(success_msg)}</div>" if success_msg else "")
                        .replace("{{error_html}}", f"<div class=\"banner error\">{html.escape(error_msg)}</div>" if error_msg else "")
                        .replace("{{employees_rows}}", rows_html)
                        .replace("{{punches_html}}", punches_html or "")
                    )
                    self._send_bytes(HTTPStatus.OK, page.encode("utf-8"))

                # Validation helpers
                import re as _re
                def _valid_name(s: str) -> bool:
                    return bool(_re.match(r"^[A-Za-z\-\s]{1,50}$", s))
                def _valid_pin(s: str) -> bool:
                    return bool(_re.match(r"^\d{4,10}$", s))
                def _check_manager(pin: str) -> bool:
                    try:
                        from ..core.repo import get_setting as _get_setting
                        from ..core.security import verify_pin as _verify
                        h = _get_setting("manager_pin_hash")
                        if not h:
                            return False
                        return _verify(pin, h)
                    except Exception:
                        return False

                # Routes
                if parsed.path == "/admin/employee/create":
                    first = (form.get("first_name") or [""])[0].strip()
                    last = (form.get("last_name") or [""])[0].strip()
                    mgr = (form.get("manager_pin") or [""])[0]
                    if not _valid_name(first) or not _valid_name(last):
                        _render_admin(error_msg="Names must be letters, spaces, hyphens (1–50 chars)")
                        return
                    if not _check_manager(mgr):
                        _render_admin(error_msg="Invalid manager PIN")
                        return
                    name = f"{first} {last}".strip()
                    try:
                        # Create with default PIN 0000 and pay_rate 0.0 (schema requires pin_hash)
                        emp_id = _repo.add_employee(name, 0.0, "0000")
                        LOGGER.info("admin.employee.create first=%s last=%s id=%s", first, last, emp_id)
                        _render_admin(success_msg=f"Employee created: {name} (id {emp_id})")
                    except Exception:
                        LOGGER.exception("admin.employee.create failed")
                        _render_admin(error_msg="Failed to create employee")
                    return

                if parsed.path == "/admin/employee/toggle":
                    emp_raw = (form.get("employee_id") or [""])[0]
                    target_raw = (form.get("active") or [""])[0]
                    mgr = (form.get("manager_pin") or [""])[0]
                    try:
                        emp_id = int(emp_raw)
                        if emp_id <= 0:
                            raise ValueError
                        target = 1 if str(target_raw) == "1" else 0
                    except Exception:
                        _render_admin(error_msg="Invalid employee or active flag")
                        return
                    if not _check_manager(mgr):
                        _render_admin(error_msg="Invalid manager PIN")
                        return
                    try:
                        with get_conn(db_path) as conn:
                            conn.execute("UPDATE employees SET active=? WHERE id=?", (target, emp_id))
                        LOGGER.info("admin.employee.toggle id=%s active=%s", emp_id, target)
                        _render_admin(success_msg=f"Employee {emp_id} {'enabled' if target==1 else 'disabled'}")
                    except Exception:
                        LOGGER.exception("admin.employee.toggle failed")
                        _render_admin(error_msg="Failed to toggle employee")
                    return

                if parsed.path == "/admin/employee/set_pin":
                    emp_raw = (form.get("employee_id") or [""])[0]
                    new_pin = (form.get("new_pin") or [""])[0]
                    mgr = (form.get("manager_pin") or [""])[0]
                    try:
                        emp_id = int(emp_raw)
                        if emp_id <= 0:
                            raise ValueError
                    except Exception:
                        _render_admin(error_msg="Invalid employee id")
                        return
                    if not _valid_pin(new_pin):
                        _render_admin(error_msg="PIN must be 4–10 digits")
                        return
                    if not _check_manager(mgr):
                        _render_admin(error_msg="Invalid manager PIN")
                        return
                    try:
                        from ..core.security import make_pin_hash as _make_hash
                        pin_h = _make_hash(new_pin)
                        with get_conn(db_path) as conn:
                            conn.execute("UPDATE employees SET pin_hash=? WHERE id=?", (pin_h, emp_id))
                        LOGGER.info("admin.employee.set_pin id=%s", emp_id)
                        _render_admin(success_msg=f"PIN updated for employee {emp_id}")
                    except Exception:
                        LOGGER.exception("admin.employee.set_pin failed")
                        _render_admin(error_msg="Failed to set PIN")
                    return

                if parsed.path == "/admin/employee/punches":
                    emp_raw = (form.get("employee_id") or [""])[0]
                    lim_raw = (form.get("limit") or ["10"])[0]
                    try:
                        emp_id = int(emp_raw)
                        if emp_id <= 0:
                            raise ValueError
                        limit = max(1, min(100, int(lim_raw)))
                    except Exception:
                        _render_admin(error_msg="Invalid employee id or limit")
                        return
                    # No manager PIN required to view punches
                    try:
                        with get_conn(db_path) as conn:
                            rows = list(
                                conn.execute(
                                    "SELECT clock_in, clock_out FROM punches WHERE employee_id=? AND clock_out IS NOT NULL ORDER BY clock_out DESC LIMIT ?",
                                    (emp_id, limit),
                                ).fetchall()
                            )
                        from ..core.reports import format_hhmm as _fmt
                        punches_html = "<div class=\"card\" style=\"margin-top:12px\">" \
                            + f"<h2 class=\"title\" style=\"font-size:20px\">Last {len(rows)} punches for #{emp_id}</h2>" \
                            + "<table class=\"table\"><thead><tr><th>Clock In</th><th>Clock Out</th><th>Duration</th></tr></thead><tbody>"
                        for ci, co in rows:
                            # compute duration
                            from datetime import datetime as _dt
                            sd = _dt.fromisoformat(str(ci).replace("Z", "+00:00"))
                            ed = _dt.fromisoformat(str(co).replace("Z", "+00:00"))
                            dur = int(max(0, (ed - sd).total_seconds()))
                            punches_html += f"<tr><td>{html.escape(str(ci))}</td><td>{html.escape(str(co))}</td><td>{_fmt(dur)}</td></tr>"
                        punches_html += "</tbody></table></div>"
                        _render_admin(punches_html=punches_html)
                    except Exception:
                        LOGGER.exception("admin.employee.punches failed")
                        _render_admin(error_msg="Failed to load punches")
                    return

                # Unknown admin route
                self._send_bytes(HTTPStatus.NOT_FOUND, b"Not Found\n", "text/plain; charset=utf-8")
                return

            # Reports handlers
            emp_raw = (form.get("employee_id") or [""])[0]
            start = (form.get("start") or [""])[0]
            end = (form.get("end") or [""])[0]
            error = ""
            try:
                emp_id_int = int(emp_raw)
                if emp_id_int <= 0:
                    raise ValueError
            except Exception:
                error = "Employee ID must be a positive integer"
            import re as _re
            if not error and (not _re.match(r"^\d{4}-\d{2}-\d{2}$", start) or not _re.match(r"^\d{4}-\d{2}-\d{2}$", end)):
                error = "Dates must be in YYYY-MM-DD"

            page = _read_text(TEMPLATES_DIR / "reports.html")
            if error:
                err_html = f"<div class=\"error\">{html.escape(error)}</div>"
                page = (
                    page
                    .replace("{{employee_id}}", html.escape(emp_raw))
                    .replace("{{start}}", html.escape(start))
                    .replace("{{end}}", html.escape(end))
                    .replace("{{error_html}}", err_html)
                    .replace("{{summary_html}}", "")
                    .replace("{{table_html}}", "")
                )
                self._send_bytes(HTTPStatus.OK, page.encode("utf-8"))
                return

            # Build data using shared reporting helpers
            import importlib as _importlib
            try:
                _importlib.reload(_reports)
            except Exception:
                pass
            totals = _reports.daily_totals(emp_id_int, start, end)
            total_seconds = _reports.period_total(emp_id_int, start, end)
            hhmm = _reports.format_hhmm(total_seconds)
            summary_html = f"<div class=\"summary\">\n<strong>Employee #{emp_id_int}</strong> <span>Period: {html.escape(start)} → {html.escape(end)}</span> <strong>Total: {hhmm}</strong>\n</div>"
            # Table rows
            rows_html = "".join(
                f"<tr><td>{d}</td><td>{_reports.format_hhmm(int(s))}</td><td>{int(s)}</td></tr>" for d, s in sorted(totals.items())
            )
            table_html = f"<table class=\"table\"><thead><tr><th>Date</th><th>Hours</th><th>Seconds</th></tr></thead><tbody>{rows_html}</tbody></table>"

            if parsed.path == "/reports/view":
                page = (
                    page
                    .replace("{{employee_id}}", html.escape(str(emp_id_int)))
                    .replace("{{start}}", html.escape(start))
                    .replace("{{end}}", html.escape(end))
                    .replace("{{error_html}}", "")
                    .replace("{{summary_html}}", summary_html)
                    .replace("{{table_html}}", table_html)
                )
                self._send_bytes(HTTPStatus.OK, page.encode("utf-8"))
                return
            else:
                # CSV response
                rows = [
                    {"date": d, "employee_id": emp_id_int, "seconds": int(s)} for d, s in sorted(totals.items())
                ]
                csv_bytes = _reports.build_csv(rows)
                filename = f"punchpad_report_{emp_id_int}_{start}_{end}.csv"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=\"{filename}\"")
                self.send_header("Content-Length", str(len(csv_bytes)))
                self.end_headers()
                try:
                    self.wfile.write(csv_bytes)
                except Exception:
                    pass
                return
        except Exception:
            LOGGER.exception("POST failed")
            self._send_bytes(HTTPStatus.INTERNAL_SERVER_ERROR, _render_result("error", "Something went wrong", self.server.redirect_seconds))

    # Reduce default logging noise
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        try:
            LOGGER.info("%s - - %s", self.address_string(), format % args)
        except Exception:
            pass


def make_server(host: str = "127.0.0.1", port: int = 8765, *, redirect_seconds: int = 2, source: str = "") -> _KioskWebServer:
    if not source:
        try:
            import socket  # local import

            source = socket.gethostname()
        except Exception:
            source = "kiosk"
    httpd = _KioskWebServer((host, int(port)), KioskRequestHandler, redirect_seconds=redirect_seconds, source=source)
    return httpd


def run_server(host: str = "127.0.0.1", port: int = 8765, *, redirect_seconds: int = 2, source: str = "") -> None:
    httpd = make_server(host, port, redirect_seconds=redirect_seconds, source=source)
    LOGGER.info("kiosk.web start host=%s port=%s", host, httpd.server_address[1])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
