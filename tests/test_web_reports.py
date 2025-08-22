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
TEST_DIR = tempfile.mkdtemp(prefix="punchpad_web_reports_")
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

class WebReportsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure DB and seed data
        with get_conn(DB_PATH) as conn:
            list(apply_migrations(conn))
            now = "2025-08-01T00:00:00Z"
            cur = conn.execute(
                "INSERT INTO employees(name, pin_hash, pay_rate, active, created_at) VALUES(?,?,?,?,?)",
                ("Test", "h", 10.0, 1, now),
            )
            cls.emp_id = int(cur.lastrowid)
            punches = [
                (cls.emp_id, "2025-08-01T09:00:00Z", "2025-08-01T17:00:00Z", "manual", None),
                (cls.emp_id, "2025-08-02T10:00:00Z", "2025-08-02T12:00:00Z", "manual", None),
                (cls.emp_id, "2025-08-03T08:00:00Z", "2025-08-03T10:00:00Z", "manual", None),
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
        httpd = make_server("127.0.0.1", port, redirect_seconds=1, source="test-web-reports")
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.1)
        return httpd, port

    def _stop_server(self, httpd):
        httpd.shutdown()
        httpd.server_close()

    def test_reports_page_and_view_and_csv(self):
        httpd, port = self._start_server()
        try:
            # GET /reports
            with urlopen(f"http://127.0.0.1:{port}/reports") as r:
                body = r.read().decode("utf-8")
                self.assertIn("Reports", body)
                self.assertIn("employee_id", body)

            # POST /reports/view
            data = urlencode({
                "employee_id": str(self.emp_id),
                "start": "2025-08-01",
                "end": "2025-08-04",
            }).encode("utf-8")
            req = Request(f"http://127.0.0.1:{port}/reports/view", data=data, method="POST")
            with urlopen(req) as r2:
                b2 = r2.read().decode("utf-8")
                self.assertIn("Employee #", b2)
                # Known day row
                self.assertIn("2025-08-01", b2)
                # Total should be 8+2+2 = 12:00
                self.assertIn("Total: 12:00", b2)

            # POST /reports/csv
            req2 = Request(f"http://127.0.0.1:{port}/reports/csv", data=data, method="POST")
            with urlopen(req2) as r3:
                ctype = r3.headers.get("Content-Type")
                self.assertTrue("text/csv" in ctype)
                csv_text = r3.read().decode("utf-8").strip().splitlines()
                # header + 3 days
                self.assertEqual(len(csv_text), 1 + 3)
                self.assertEqual(csv_text[0], "date,employee_id,seconds")
        finally:
            self._stop_server(httpd)

    def test_invalid_inputs_show_error(self):
        httpd, port = self._start_server()
        try:
            bad = urlencode({"employee_id": "0", "start": "2025-08-01", "end": "2025-08-04"}).encode("utf-8")
            with urlopen(Request(f"http://127.0.0.1:{port}/reports/view", data=bad, method="POST")) as r:
                html = r.read().decode("utf-8")
                self.assertIn("Employee ID must be a positive integer", html)

            bad2 = urlencode({"employee_id": "1", "start": "bad", "end": "2025-08-04"}).encode("utf-8")
            with urlopen(Request(f"http://127.0.0.1:{port}/reports/view", data=bad2, method="POST")) as r2:
                html2 = r2.read().decode("utf-8")
                self.assertIn("Dates must be in YYYY-MM-DD", html2)
        finally:
            self._stop_server(httpd)

if __name__ == "__main__":
    unittest.main()
