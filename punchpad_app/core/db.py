from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .paths import DB_PATH


LOGGER = logging.getLogger(__name__)


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        check_same_thread=False,
        isolation_level=None,  # autocommit mode; we'll use explicit BEGIN where needed
    )
    conn.row_factory = sqlite3.Row

    # Apply required PRAGMAs
    pragmas: Sequence[str] = (
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=FULL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA busy_timeout=5000",
    )
    for pragma in pragmas:
        conn.execute(pragma)

    # Log resolved PRAGMA values
    _log_connection_pragmas(conn)
    return conn


def _log_connection_pragmas(conn: sqlite3.Connection) -> None:
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        LOGGER.info(
            "SQLite PRAGMAs active: journal_mode=%s, synchronous=%s, foreign_keys=%s, busy_timeout=%s",
            journal_mode,
            synchronous,
            foreign_keys,
            busy_timeout,
        )
    except Exception:
        LOGGER.exception("Failed to read PRAGMA values for logging")


def list_available_migrations(migrations_dir: Path | None = None) -> List[Tuple[int, Path]]:
    if migrations_dir is None:
        migrations_dir = Path(__file__).parent / "migrations"
    migrations: List[Tuple[int, Path]] = []
    if not migrations_dir.exists():
        return migrations
    pattern = re.compile(r"^(\d{4,})_.*\.sql$")
    for path in sorted(migrations_dir.glob("*.sql")):
        match = pattern.match(path.name)
        if not match:
            continue
        version = int(match.group(1))
        migrations.append((version, path))
    migrations.sort(key=lambda x: x[0])
    return migrations


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations(
            version INTEGER PRIMARY KEY
        )
        """
    )


def apply_migrations(conn: sqlite3.Connection) -> Iterable[int]:
    """Apply any pending migrations. Yields applied version numbers in order.

    Runs each migration in a single transaction using executescript's implicit
    transaction when autocommit is enabled. Records the applied version atomically.
    """
    _ensure_schema_migrations_table(conn)

    applied_versions = {
        row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    }

    applied_now: List[int] = []
    for version, path in list_available_migrations():
        if version in applied_versions:
            continue
        sql = path.read_text(encoding="utf-8")
        LOGGER.info("Applying migration %s from %s", version, path.name)
        try:
            # Execute migration and record version atomically
            script = sql + f"\nINSERT INTO schema_migrations(version) VALUES ({version});\n"
            conn.executescript(script)
        except Exception:
            LOGGER.exception("Migration %s failed; rolled back", version)
            raise
        applied_now.append(version)
    for v in applied_now:
        yield v


def seed_default_settings(conn: sqlite3.Connection) -> None:
    """Insert default settings if missing. Values stored as strings."""
    defaults = {
        "pay_period": "weekly",
        "week_start": "Monday",
        "rounding_minutes": "0",
        "overtime_policy": "none",
        "ui.idle_logout_seconds": "60",
        "ui.keypad_mode": "auto",
        "ui.keypad_hotplug_poll_seconds": "2",
        "backups.keep_days": "90",
        "jobs.reconcile_interval_seconds": "5",
    }

    existing = {
        row[0] for row in conn.execute("SELECT key FROM settings WHERE key IN (%s)" % (
            ",".join(["?"] * len(defaults))
        ), tuple(defaults.keys()))
    }

    to_insert = [(k, v) for k, v in defaults.items() if k not in existing]
    if not to_insert:
        return

    LOGGER.info("Seeding default settings for keys: %s", ", ".join(k for k, _ in to_insert))
    conn.executemany("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", to_insert)
