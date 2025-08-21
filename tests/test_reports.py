import os
import tempfile
import unittest
from pathlib import Path

# Set test data dir BEFORE importing app modules
TEST_DIR = tempfile.mkdtemp(prefix="punchpad_test_")
os.environ["PUNCHPAD_DATA_DIR"] = TEST_DIR

from punchpad_app.core.paths import DB_PATH  # noqa: E402
from punchpad_app.core.db import get_conn, apply_migrations  # noqa: E402
from punchpad_app.core.reports import daily_totals, period_total, to_csv  # noqa: E402


class ReportsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure DB exists and schema applied
        with get_conn(DB_PATH) as conn:
            list(apply_migrations(conn))
            # Seed one employee id=1
            conn.execute(
                "INSERT INTO employees(id, name, pin_hash, pay_rate, active, created_at) VALUES(?,?,?,?,?,?)",
                (1, "Alice", "x", 20.0, 1, "2025-01-01T00:00:00Z"),
            )
            # Seed punches spanning multiple days (UTC)
            punches = [
                # 2025-08-01: 09:00 - 17:00 (8h)
                (1, "2025-08-01T09:00:00Z", "2025-08-01T17:00:00Z", "manual", None),
                # 2025-08-02: 10:30 - 15:00 (4.5h)
                (1, "2025-08-02T10:30:00Z", "2025-08-02T15:00:00Z", "manual", None),
                # Cross day: 2025-08-02 22:00 - 2025-08-03 02:00 (4h)
                (1, "2025-08-02T22:00:00Z", "2025-08-03T02:00:00Z", "manual", None),
            ]
            conn.executemany(
                "INSERT INTO punches(employee_id, clock_in, clock_out, method, note) VALUES(?,?,?,?,?)",
                punches,
            )

    def test_daily_totals(self):
        totals = daily_totals(1, "2025-08-01", "2025-08-04")
        # Expect: 2025-08-01: 8h, 2025-08-02: 6.5h (4.5 + 2), 2025-08-03: 2h
        self.assertEqual(totals.get("2025-08-01"), 8 * 3600)
        self.assertEqual(totals.get("2025-08-02"), int(6.5 * 3600))
        self.assertEqual(totals.get("2025-08-03"), 2 * 3600)

    def test_period_total(self):
        total = period_total(1, "2025-08-01", "2025-08-04")
        self.assertEqual(total, (8 + 6.5 + 2) * 3600)

    def test_csv_export(self):
        totals = daily_totals(1, "2025-08-01", "2025-08-04")
        rows = [
            {"date": day, "employee_id": 1, "seconds": int(secs)}
            for day, secs in sorted(totals.items())
        ]
        out_path = Path(TEST_DIR) / "report.csv"
        to_csv(rows, str(out_path))
        self.assertTrue(out_path.exists())
        content = out_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(content[0], "date,employee_id,seconds")
        # Check there are 3 data rows
        self.assertEqual(len(content) - 1, 3)
        # Basic sanity: first row starts with 2025-08-01
        self.assertTrue(content[1].startswith("2025-08-01,"))


if __name__ == "__main__":
    unittest.main()  # pragma: no cover
