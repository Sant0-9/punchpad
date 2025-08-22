import os
import socket
import threading
import time
import unittest
import importlib
from urllib.request import urlopen, Request
from urllib.parse import urlencode
import tempfile

# Ensure test data dir BEFORE importing app modules
TEST_DIR = tempfile.mkdtemp(prefix="punchpad_web_")
os.environ["PUNCHPAD_DATA_DIR"] = TEST_DIR

import punchpad_app.core.paths as _paths  # noqa: E402
importlib.reload(_paths)
import punchpad_app.core.db as _db  # noqa: E402
importlib.reload(_db)
import punchpad_app.core.repo as _repo  # noqa: E402
importlib.reload(_repo)
import punchpad_app.core.security as _sec  # noqa: E402
importlib.reload(_sec)
from punchpad_app.web.server import make_server  # noqa: E402
from punchpad_app.core.db import get_conn, apply_migrations  # noqa: E402
from punchpad_app.core.repo import add_employee  # noqa: E402
from punchpad_app.core.paths import DB_PATH  # noqa: E402


class WebUITestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bootstrap in a subprocess to ensure schema exists before importing helpers
        code = (
            "import os; os.environ['PUNCHPAD_DATA_DIR']=r'%s'; "
            "from punchpad_app.core.db import get_conn, apply_migrations, seed_default_settings; from punchpad_app.core.paths import DB_PATH; "
            "from punchpad_app.core.repo import add_employee; "
            "\nwith get_conn(DB_PATH) as conn: list(apply_migrations(conn)); seed_default_settings(conn); print(add_employee('Eve',22.0,'2468'))"
        ) % TEST_DIR
        import subprocess, sys
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PUNCHPAD_DATA_DIR": TEST_DIR}, timeout=15)
        if out.returncode != 0:
            raise RuntimeError(f"bootstrap failed: {out.stderr}")
        try:
            cls.emp_id = int(out.stdout.strip().splitlines()[-1])
        except Exception:
            cls.emp_id = 1

    def _free_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        addr, port = s.getsockname()
        s.close()
        return port

    def _start_server(self, redirect_seconds=1):
        port = self._free_port()
        httpd = make_server("127.0.0.1", port, redirect_seconds=redirect_seconds, source="test-web")
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        # Wait a tick for server to be ready
        time.sleep(0.1)
        return httpd, port

    def _stop_server(self, httpd):
        httpd.shutdown()
        httpd.server_close()

    def test_index_and_static(self):
        httpd, port = self._start_server()
        try:
            with urlopen(f"http://127.0.0.1:{port}/") as r:
                body = r.read().decode("utf-8")
                self.assertIn("PunchPad", body)
                self.assertIn("<form", body)
                self.assertIn("action=\"/pin\"", body)
            with urlopen(f"http://127.0.0.1:{port}/static/style.css") as r2:
                css = r2.read().decode("utf-8")
                self.assertIn(".banner", css)
        finally:
            self._stop_server(httpd)

    def test_pin_flow_good_then_duplicate_then_lock(self):
        httpd, port = self._start_server(redirect_seconds=1)
        try:
            # Good PIN
            data = urlencode({"pin": "2468", "source": "test-web"}).encode("utf-8")
            req = Request(f"http://127.0.0.1:{port}/pin", data=data, method="POST")
            with urlopen(req) as r:
                body = r.read().decode("utf-8")
                # Accept success banners or verify via DB state if banner not present
                if ("PUNCHED IN" not in body) and ("PUNCHED OUT" not in body):
                    import os as _os
                    import importlib as _importlib
                    _os.environ["PUNCHPAD_DATA_DIR"] = TEST_DIR
                    import punchpad_app.core.paths as _paths
                    _importlib.reload(_paths)
                    import punchpad_app.core.db as _db
                    _importlib.reload(_db)
                    from punchpad_app.core.db import get_conn
                    from punchpad_app.core.paths import DB_PATH as _DB
                    with get_conn(_DB) as conn:
                        row = conn.execute(
                            "SELECT COUNT(*) FROM punches WHERE employee_id=?",
                            (self.emp_id,),
                        ).fetchone()
                        self.assertGreater(row[0], 0, msg=f"Expected a punch recorded; body={body[:200]}")
            # Immediately again: OUT or Duplicate depending on state/seconds; accept either
            with urlopen(Request(f"http://127.0.0.1:{port}/pin", data=data, method="POST")) as r2:
                b2 = r2.read().decode("utf-8")
                self.assertTrue("PUNCHED OUT" in b2 or "Duplicate" in b2)

            # Bad PIN attempts to trigger lockout quickly: use settings defaults (5 per 300s);
            # We'll exceed by sending 6 bad attempts and then expect Locked page.
            bad = urlencode({"pin": "0000", "source": "test-web"}).encode("utf-8")
            for _ in range(6):
                urlopen(Request(f"http://127.0.0.1:{port}/pin", data=bad, method="POST")).read()
            with urlopen(Request(f"http://127.0.0.1:{port}/pin", data=bad, method="POST")) as r3:
                b3 = r3.read().decode("utf-8")
                self.assertIn("Locked", b3)
        finally:
            self._stop_server(httpd)


if __name__ == "__main__":
    unittest.main()
