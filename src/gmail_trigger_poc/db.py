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
    """Open the QuoteFlow SQLite DB with row access by name + FK enforcement."""
    settings = settings or get_settings()
    conn = sqlite3.connect(settings.db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(settings: Settings | None = None, conn: sqlite3.Connection | None = None) -> None:
    """Create every table/index if missing. Idempotent (schema uses IF NOT EXISTS)."""
    settings = settings or get_settings()
    own = conn is None
    conn = conn or connect(settings)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
