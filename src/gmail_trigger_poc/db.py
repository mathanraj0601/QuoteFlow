"""SQLite relational store — connection + schema init.

The schema is shipped verbatim from the project's canonical
`v2SQ-quoteflow_schema_without_extraction.sql` (see `schema.sql` in this package
for the one additive column). SQLite is the source of truth for all IDs; vectors
live in ChromaDB (see `vectorstore.py`).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from gmail_trigger_poc.config import Settings, get_settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    """Open the QuoteFlow SQLite DB with row access by name + FK enforcement.

    The poller thread and the HTTP API each hold their own connection, so a
    write-write overlap is possible; `busy_timeout` makes the loser wait rather
    than fail immediately, and WAL lets readers run during a write.
    """
    settings = settings or get_settings()
    conn = sqlite3.connect(settings.db_file, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:  # pragma: no cover - e.g. network FS
        pass
    return conn


# Additive columns that a pre-existing DB (created before this column landed)
# won't have. `CREATE TABLE IF NOT EXISTS` can't add them to a live table, so
# `init_db` ALTERs them in. Column -> SQLite type/default clause.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "email_threads": {
        "status": "TEXT",
        "pipeline_stage": "TEXT",
        "pipeline_updated_at": "TIMESTAMP",
        "drafts_total": "INTEGER",
        "matched_suppliers_json": "TEXT",
        "draft_subject": "TEXT",
        "draft_body_template": "TEXT",
        "draft_fields_json": "TEXT",
        "draft_edit_mode": "TEXT",
    },
}


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    """ALTER in any `_ADDITIVE_COLUMNS` a pre-existing DB is missing. Idempotent."""
    for table, columns in _ADDITIVE_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                logger.info("migrated: %s.%s added", table, name)


def init_db(settings: Settings | None = None, conn: sqlite3.Connection | None = None) -> None:
    """Create every table/index if missing, then apply additive column migrations.

    Idempotent: the schema uses IF NOT EXISTS, and `_apply_additive_migrations`
    only ALTERs columns that a pre-existing DB is missing.
    """
    settings = settings or get_settings()
    own = conn is None
    conn = conn or connect(settings)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _apply_additive_migrations(conn)
        conn.commit()
        logger.debug("schema applied from %s", SCHEMA_PATH.name)
    finally:
        if own:
            conn.close()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per user table — used by the seed script's sanity check."""
    names = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    return {n: conn.execute(f"SELECT COUNT(*) AS c FROM {n}").fetchone()["c"] for n in names}
