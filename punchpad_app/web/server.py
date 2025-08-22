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
            if parsed.path != "/pin":
                self._send_bytes(HTTPStatus.NOT_FOUND, b"Not Found\n", "text/plain; charset=utf-8")
                return

            # Read form body (application/x-www-form-urlencoded)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
            except Exception:
                raw = b""
            form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            pin = (form.get("pin") or [""])[0]
            source = (form.get("source") or [self.server.source])[0] or self.server.source
            now_iso = _utc_now_iso_z()

            # Do not log the PIN; only log minimal info
            LOGGER.info("kiosk.web pin received source=%s len=%s", source, len(pin))

            # Ensure schema/defaults applied (idempotent)
            with get_conn(db_path) as conn:
                list(apply_migrations(conn))
                seed_default_settings(conn)
            # Lockout check
            with get_conn(db_path) as conn:
                locked, _until = sec.check_pin_lockout(conn, source, now_iso)
            if locked:
                body = _render_result("locked", "Locked — too many bad attempts", self.server.redirect_seconds)
                self._send_bytes(HTTPStatus.OK, body)
                LOGGER.info("kiosk.web result status=locked employee_id=- reason=lockout")
                return

            # Verify PIN
            with get_conn(db_path) as conn:
                emp_id: Optional[int] = sec.verify_employee_pin(conn, pin)
                if emp_id is None:
                    sec.record_pin_attempt(conn, source, now_iso, False, None, "bad_pin")
                    body = _render_result("blocked", "Invalid PIN", self.server.redirect_seconds)
                    self._send_bytes(HTTPStatus.OK, body)
                    LOGGER.info("kiosk.web result status=blocked employee_id=- reason=bad_pin")
                    return

            # Success attempt
            with get_conn(db_path) as conn:
                sec.record_pin_attempt(conn, source, now_iso, True, emp_id, None)

            # Toggle punch
            with get_conn(db_path) as conn:
                res = _punches.toggle_punch(conn, int(emp_id), method="kiosk", note=None, now_iso=now_iso)
            action = res.get("action")
            status = res.get("status")
            queued = status == "queued"
            if status == "blocked":
                retry = res.get("retry_after_seconds")
                msg = f"Duplicate punch blocked — try again in ~{int(retry)}s" if retry is not None else "Duplicate punch blocked"
                body = _render_result("blocked", msg, self.server.redirect_seconds)
                self._send_bytes(HTTPStatus.OK, body)
                LOGGER.info("kiosk.web result status=blocked employee_id=%s reason=duplicate", emp_id)
                return

            # Success or queued
            local_time = _local_hhmm()
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
