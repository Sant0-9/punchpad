import os
import socket
import threading
import time
import unittest
import importlib
from urllib.request import urlopen, Request
from urllib.parse import urlencode
import tempfile

# Isolate data dir
TEST_DIR = tempfile.mkdtemp(prefix="punchpad_web_admin_")
os.environ["PUNCHPAD_DATA_DIR"] = TEST_DIR

import punchpad_app.core.paths as _paths  # noqa: E402
importlib.reload(_paths)
import punchpad_app.core.db as _db  # noqa: E402
importlib.reload(_db)
import punchpad_app.core.repo as _repo  # noqa: E402
importlib.reload(_repo)
from punchpad_app.core.db import get_conn, apply_migrations  # noqa: E402
from punchpad_app.core.paths import DB_PATH  # noqa: E402
from punchpad_app.web.server import make_server  # noqa: E402
from punchpad_app.core.security import make_pin_hash  # noqa: E402


class WebAdminTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure DB and seed settings/employee
        with get_conn(DB_PATH) as conn:
            list(apply_migrations(conn))
            # Seed manager PIN hash
            mph = make_pin_hash("4321")
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("manager_pin_hash", mph))
            # Seed one employee
            now = "2025-08-01T00:00:00Z"
            cur = conn.execute(
                "INSERT INTO employees(name, pin_hash, pay_rate, active, created_at) VALUES(?,?,?,?,?)",
                ("Alice Tester", make_pin_hash("1111"), 10.0, 1, now),
            )
            cls.emp_id = int(cur.lastrowid)
            punches = [
                (cls.emp_id, "2025-08-01T09:00:00Z", "2025-08-01T17:00:00Z", "manual", None),
                (cls.emp_id, "2025-08-02T10:00:00Z", "2025-08-02T12:00:00Z", "manual", None),
            ]
            conn.executemany(
                "INSERT INTO punches(employee_id, clock_in, clock_out, method, note) VALUES(?,?,?,?,?)",
                punches,
            )

    def _free_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        addr, port = s.getsockname()
        s.close()
        return port

    def _start_server(self):
        port = self._free_port()
        httpd = make_server("127.0.0.1", port, redirect_seconds=1, source="test-web-admin")
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.1)
        return httpd, port

    def _stop_server(self, httpd):
        httpd.shutdown()
        httpd.server_close()

    def test_admin_page_and_actions(self):
        httpd, port = self._start_server()
        try:
            # GET /admin
            with urlopen(f"http://127.0.0.1:{port}/admin") as r:
                body = r.read().decode("utf-8")
                self.assertIn("Add Employee", body)

            # Create employee
            data = urlencode({
                "first_name": "Bob",
                "last_name": "Builder",
                "manager_pin": "4321",
            }).encode("utf-8")
            req = Request(f"http://127.0.0.1:{port}/admin/employee/create", data=data, method="POST")
            with urlopen(req) as r2:
                b2 = r2.read().decode("utf-8")
                self.assertIn("Employee created", b2)

            # Confirm appears in GET
            with urlopen(f"http://127.0.0.1:{port}/admin") as r3:
                b3 = r3.read().decode("utf-8")
                self.assertIn("Bob Builder", b3)

            # Toggle active off
            # Need to find new employee id
            with get_conn(DB_PATH) as conn:
                row = conn.execute("SELECT id FROM employees WHERE name=?", ("Bob Builder",)).fetchone()
                new_emp_id = int(row[0])
            data2 = urlencode({
                "employee_id": str(new_emp_id),
                "active": "0",
                "manager_pin": "4321",
            }).encode("utf-8")
            req2 = Request(f"http://127.0.0.1:{port}/admin/employee/toggle", data=data2, method="POST")
            with urlopen(req2) as r4:
                b4 = r4.read().decode("utf-8")
                self.assertIn("disabled", b4)

            # Set PIN
            data3 = urlencode({
                "employee_id": str(new_emp_id),
                "new_pin": "9876",
                "manager_pin": "4321",
            }).encode("utf-8")
            req3 = Request(f"http://127.0.0.1:{port}/admin/employee/set_pin", data=data3, method="POST")
            with urlopen(req3) as r5:
                b5 = r5.read().decode("utf-8")
                self.assertIn("PIN updated", b5)

            # View punches
            data4 = urlencode({"employee_id": str(self.emp_id), "limit": "2"}).encode("utf-8")
            req4 = Request(f"http://127.0.0.1:{port}/admin/employee/punches", data=data4, method="POST")
            with urlopen(req4) as r6:
                b6 = r6.read().decode("utf-8")
                self.assertIn("Last 2 punches", b6)
                self.assertIn("Duration", b6)

            # Bad manager pin
            bad = urlencode({
                "first_name": "X",
                "last_name": "Y",
                "manager_pin": "0000",
            }).encode("utf-8")
            with urlopen(Request(f"http://127.0.0.1:{port}/admin/employee/create", data=bad, method="POST")) as r7:
                b7 = r7.read().decode("utf-8")
                self.assertIn("Invalid manager PIN", b7)
        finally:
            self._stop_server(httpd)


if __name__ == "__main__":
    unittest.main()
