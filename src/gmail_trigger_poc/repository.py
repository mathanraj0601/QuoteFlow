"""SQLite reads/writes for the live pipeline (clients, email_threads, SQRs).

Keeps all the INSERT/SELECT shapes for `supplier_quote_requests` and friends in
one place so `pipeline.py` reads as the orchestration it is.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from datetime import datetime, timezone
from email.utils import parseaddr

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bare_email(value: str | None) -> str:
    return (parseaddr(value or "")[1] or (value or "")).strip().lower()


def derive_thread_id(gmail_thread_id: str | None, gmail_message_id: str | None) -> int:
    """Stable non-negative int for the canonical schema's INTEGER thread_id.

    Same Gmail thread -> same int (so a reply groups with its enquiry). Gmail ids
    are hex; fall back to CRC32 for anything that isn't.
    """
    token = (gmail_thread_id or gmail_message_id or "").strip()
    if not token:
        return 0
    try:
        return int(token, 16) & 0x7FFFFFFF
    except ValueError:
        return zlib.crc32(token.encode()) & 0x7FFFFFFF


# --------------------------------------------------------------------------- #
# clients
# --------------------------------------------------------------------------- #


def get_or_create_client(extraction, from_email: str, conn: sqlite3.Connection) -> int:
    email = bare_email(from_email) or "unknown@unknown.invalid"
    row = conn.execute("SELECT id FROM clients WHERE contact_email = ?", (email,)).fetchone()
    if row:
        return row["id"]
    name = extraction.client_name or (email.split("@")[0] if email else "Unknown")
    company = extraction.client_company or (
        email.split("@")[1] if "@" in email else "Unknown"
    )
    cur = conn.execute(
        "INSERT INTO clients (name, company, contact_email, region, order_history_count) "
        "VALUES (?, ?, ?, ?, 0)",
        (name, company, email, extraction.destination),
    )
    logger.info("created client id=%s <%s> (%s)", cur.lastrowid, email, company)
    return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# email_threads
# --------------------------------------------------------------------------- #


def find_thread_by_message_id(
    gmail_message_id: str | None, conn: sqlite3.Connection
) -> sqlite3.Row | None:
    if not gmail_message_id:
        return None
    return conn.execute(
        "SELECT * FROM email_threads WHERE gmail_message_id = ?", (gmail_message_id,)
    ).fetchone()


def insert_inbound_thread(
    email_data: dict, extraction, conn: sqlite3.Connection, *, broker_email: str
) -> int:
    """Insert (or return existing) the inbound email_threads row. Returns its id."""
    gmail_message_id = email_data.get("id")
    existing = find_thread_by_message_id(gmail_message_id, conn)
    if existing:
        return existing["id"]

    gmail_thread_id = email_data.get("gmail_thread_id") or email_data.get("thread_id")
    thread_id = derive_thread_id(str(gmail_thread_id) if gmail_thread_id else None, gmail_message_id)
    cur = conn.execute(
        """
        INSERT INTO email_threads
            (gmail_message_id, thread_id, gmail_thread_id, from_email, to_email, subject,
             body_text, direction, extracted_data_json, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'inbound', ?, ?)
        """,
        (
            gmail_message_id,
            thread_id,
            str(gmail_thread_id) if gmail_thread_id else None,
            bare_email(email_data.get("from")),
            bare_email(email_data.get("to")) or broker_email,
            email_data.get("subject"),
            email_data.get("body"),
            json.dumps(extraction.as_dict()),
            _now(),
        ),
    )
    logger.info(
        "email_threads row id=%s (thread_id=%s) scenario=%s",
        cur.lastrowid, thread_id, extraction.scenario,
    )
    return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# supplier_quote_requests
# --------------------------------------------------------------------------- #


def _specs_summary(extraction, candidate: dict) -> str:
    parts = [
        p
        for p in (
            extraction.specifications or candidate.get("product_specifications"),
            extraction.delivery_terms,
            extraction.destination,
            f"urgency: {extraction.urgency}" if extraction.urgency != "normal" else None,
        )
        if p
    ]
    return "; ".join(parts)


def create_supplier_quote_request(
    *,
    email_thread_id: int,
    client_id: int,
    candidate: dict,
    draft: dict,
    extraction,
    match_score: float,
    conn: sqlite3.Connection,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO supplier_quote_requests
            (email_thread_id, client_id, supplier_id, product_id, quantity, specs_summary,
             match_score, draft_to, draft_subject, draft_body, draft_body_template,
             draft_fields_json, draft_edit_mode, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fields', 'draft')
        """,
        (
            email_thread_id,
            client_id,
            candidate["supplier_id"],
            candidate.get("product_id"),
            extraction.quantity,
            _specs_summary(extraction, candidate),
            match_score,
            draft["to"],
            draft["subject"],
            draft["body"],
            draft["body_template"],
            json.dumps(draft["fields"]),
        ),
    )
    return int(cur.lastrowid)


def _row_to_draft(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["draft_fields"] = json.loads(d.pop("draft_fields_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["draft_fields"] = []
    return d


def list_supplier_drafts(email_thread_id: int, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT sqr.*, s.name AS supplier_name, s.rating AS supplier_rating
        FROM supplier_quote_requests sqr
        JOIN suppliers s ON s.id = sqr.supplier_id
        WHERE sqr.email_thread_id = ?
        ORDER BY sqr.match_score DESC, sqr.id
        """,
        (email_thread_id,),
    ).fetchall()
    return [_row_to_draft(r) for r in rows]


def get_supplier_draft(sqr_id: int, conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """
        SELECT sqr.*, s.name AS supplier_name, s.rating AS supplier_rating
        FROM supplier_quote_requests sqr
        JOIN suppliers s ON s.id = sqr.supplier_id
        WHERE sqr.id = ?
        """,
        (sqr_id,),
    ).fetchone()
    return _row_to_draft(row) if row else None


def mark_draft_sent(sqr_id: int, conn: sqlite3.Connection) -> dict | None:
    conn.execute(
        "UPDATE supplier_quote_requests SET status = 'sent', sent_at = ? "
        "WHERE id = ? AND status != 'sent'",
        (_now(), sqr_id),
    )
    conn.commit()
    return get_supplier_draft(sqr_id, conn)


def enquiry_progress(email_thread_id: int, conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent "
        "FROM supplier_quote_requests WHERE email_thread_id = ?",
        (email_thread_id,),
    ).fetchone()
    total = row["total"] or 0
    sent = row["sent"] or 0
    return {"sent": sent, "total": total}
