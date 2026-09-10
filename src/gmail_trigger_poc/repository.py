"""SQLite reads/writes for the live pipeline (clients, email_threads, SQRs).

Keeps all the INSERT/SELECT shapes for `supplier_quote_requests` and friends in
one place so `pipeline.py` reads as the orchestration it is.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from datetime import UTC, datetime
from email.utils import parseaddr

from gmail_trigger_poc.llm import render_body, substitute

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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

    def _lookup() -> int | None:
        row = conn.execute(
            "SELECT id FROM clients WHERE contact_email = ?", (email,)
        ).fetchone()
        return row["id"] if row else None

    existing = _lookup()
    if existing is not None:
        return existing

    name = extraction.client_name or (email.split("@")[0] if email else "Unknown")
    company = extraction.client_company or (
        email.split("@")[1] if "@" in email else "Unknown"
    )
    try:
        cur = conn.execute(
            "INSERT INTO clients (name, company, contact_email, region, order_history_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (name, company, email, extraction.destination),
        )
    except sqlite3.IntegrityError:
        # lost a race (or contact_email already present under a different case path)
        found = _lookup()
        if found is None:
            raise
        return found
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


def create_received_thread(
    email_data: dict, conn: sqlite3.Connection, *, broker_email: str
) -> int:
    """Insert the inbound row the instant an email lands — before classify/extract.

    Born 'new' / 'received' with no `extracted_data_json` yet, so the Desk shows
    "Enquiry received" in real time. `attach_extraction` fills the parsed fields
    and moves it to 'processing' / 'extracted' (or 'logged') once STEP 1 runs.
    Idempotent on `gmail_message_id` (UNIQUE): a redelivery returns the same id.
    """
    gmail_message_id = email_data.get("id")
    existing = find_thread_by_message_id(gmail_message_id, conn)
    if existing:
        return existing["id"]

    gmail_thread_id = email_data.get("gmail_thread_id") or email_data.get("thread_id")
    thread_id = derive_thread_id(
        str(gmail_thread_id) if gmail_thread_id else None, gmail_message_id
    )
    try:
        cur = conn.execute(
            """
            INSERT INTO email_threads
                (gmail_message_id, thread_id, gmail_thread_id, from_email, to_email, subject,
                 body_text, direction, processed_at, status, pipeline_stage, pipeline_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'inbound', ?, 'new', 'received', ?)
            """,
            (
                gmail_message_id,
                thread_id,
                str(gmail_thread_id) if gmail_thread_id else None,
                bare_email(email_data.get("from")),
                bare_email(email_data.get("to")) or broker_email,
                email_data.get("subject"),
                email_data.get("body"),
                _now(),
                _now(),
            ),
        )
    except sqlite3.IntegrityError:
        raced = find_thread_by_message_id(gmail_message_id, conn)
        if raced is None:
            raise
        return raced["id"]
    logger.info("email_threads row id=%s (thread_id=%s) status=new stage=received",
                cur.lastrowid, thread_id)
    return int(cur.lastrowid)


def attach_extraction(
    email_thread_id: int,
    extraction,
    conn: sqlite3.Connection,
    *,
    status: str = "processing",
    pipeline_stage: str = "extracted",
    commit: bool = True,
) -> None:
    """Land the classify+extract result on a row created by `create_received_thread`."""
    conn.execute(
        "UPDATE email_threads SET extracted_data_json = ?, status = ?, "
        "pipeline_stage = ?, pipeline_updated_at = ? WHERE id = ?",
        (json.dumps(extraction.as_dict()), status, pipeline_stage, _now(), email_thread_id),
    )
    if commit:
        conn.commit()
    logger.info("email_thread id=%s extraction attached (scenario=%s status=%s)",
                email_thread_id, extraction.scenario, status)


def insert_inbound_thread(
    email_data: dict,
    extraction,
    conn: sqlite3.Connection,
    *,
    broker_email: str,
    status: str = "processing",
    pipeline_stage: str = "extracted",
) -> int:
    """Insert (or return existing) the inbound email_threads row. Returns its id.

    `status` / `pipeline_stage` seed the v2SQ lifecycle columns. The pipeline
    inserts the row only after classify+extract has run, so the row is born at
    'processing'/'extracted' for an enquiry and 'logged'/None for a non-enquiry;
    `set_pipeline_stage` / `finalize_thread_status` move it on from there.
    """
    gmail_message_id = email_data.get("id")
    existing = find_thread_by_message_id(gmail_message_id, conn)
    if existing:
        return existing["id"]

    gmail_thread_id = email_data.get("gmail_thread_id") or email_data.get("thread_id")
    thread_id = derive_thread_id(str(gmail_thread_id) if gmail_thread_id else None, gmail_message_id)
    try:
        cur = conn.execute(
            """
            INSERT INTO email_threads
                (gmail_message_id, thread_id, gmail_thread_id, from_email, to_email, subject,
                 body_text, direction, extracted_data_json, processed_at,
                 status, pipeline_stage, pipeline_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'inbound', ?, ?, ?, ?, ?)
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
                status,
                pipeline_stage,
                _now(),
            ),
        )
    except sqlite3.IntegrityError:
        # concurrent processing of the same gmail_message_id (UNIQUE) — reuse the row
        raced = find_thread_by_message_id(gmail_message_id, conn)
        if raced is None:
            raise
        return raced["id"]
    logger.info(
        "email_threads row id=%s (thread_id=%s) scenario=%s status=%s",
        cur.lastrowid, thread_id, extraction.scenario, status,
    )
    return int(cur.lastrowid)


# Order of the processing-trace stages shown in the UI. 'drafting' sits between
# 'matched' and 'drafted': suppliers are known, per-supplier outreach in flight.
PIPELINE_STAGES = ("received", "extracted", "matched", "drafting", "drafted")


def set_pipeline_stage(
    email_thread_id: int, stage: str, conn: sqlite3.Connection, *, commit: bool = True
) -> None:
    """Advance a still-processing enquiry to `stage` (keeps status='processing')."""
    conn.execute(
        "UPDATE email_threads SET status = 'processing', pipeline_stage = ?, "
        "pipeline_updated_at = ? WHERE id = ?",
        (stage, _now(), email_thread_id),
    )
    if commit:
        conn.commit()


def set_drafts_total(
    email_thread_id: int, total: int, conn: sqlite3.Connection, *, commit: bool = True
) -> None:
    """Record how many matched suppliers this enquiry will draft to (set once)."""
    conn.execute(
        "UPDATE email_threads SET drafts_total = ? WHERE id = ?", (total, email_thread_id)
    )
    if commit:
        conn.commit()


def draft_progress(email_thread_id: int, conn: sqlite3.Connection) -> dict:
    """{done, total} for per-supplier drafting. `done` = committed draft rows so
    far; `total` = drafts_total if set, else `done` (so a finished enquiry reads
    N/N)."""
    done = conn.execute(
        "SELECT COUNT(*) AS c FROM supplier_quote_requests WHERE email_thread_id = ?",
        (email_thread_id,),
    ).fetchone()["c"]
    row = conn.execute(
        "SELECT drafts_total FROM email_threads WHERE id = ?", (email_thread_id,)
    ).fetchone()
    total = row["drafts_total"] if row and row["drafts_total"] is not None else done
    return {"done": done, "total": max(total, done)}


_MATCHED_KEYS = ("supplier_id", "supplier_name", "supplier_email", "match_score")


def set_matched_suppliers(
    email_thread_id: int, candidates: list[dict], conn: sqlite3.Connection, *, commit: bool = True
) -> None:
    """Snapshot who cleared the match threshold, so the trace can name them
    after a page refresh (not only on the live WebSocket tick)."""
    slim = [{k: c.get(k) for k in _MATCHED_KEYS} for c in candidates]
    conn.execute(
        "UPDATE email_threads SET matched_suppliers_json = ? WHERE id = ?",
        (json.dumps(slim), email_thread_id),
    )
    if commit:
        conn.commit()


def get_matched_suppliers(email_thread_id: int, conn: sqlite3.Connection) -> list[dict]:
    row = conn.execute(
        "SELECT matched_suppliers_json FROM email_threads WHERE id = ?", (email_thread_id,)
    ).fetchone()
    if not row or not row["matched_suppliers_json"]:
        return []
    try:
        data = json.loads(row["matched_suppliers_json"])
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# --------------------------------------------------------------------------- #
# shared enquiry outreach draft (one editable email per enquiry)
# --------------------------------------------------------------------------- #


def set_enquiry_draft(
    email_thread_id: int,
    draft: dict,
    conn: sqlite3.Connection,
    *,
    edit_mode: str = "fields",
    commit: bool = True,
) -> None:
    """Store the one shared outreach draft (subject / {{token}} template / fields)
    on the enquiry row. `draft` is the shape llm.draft_shared_outreach returns."""
    conn.execute(
        "UPDATE email_threads SET draft_subject = ?, draft_body_template = ?, "
        "draft_fields_json = ?, draft_edit_mode = ? WHERE id = ?",
        (
            draft.get("subject") or "",
            draft.get("body_template") or "",
            json.dumps(draft.get("fields") or []),
            edit_mode,
            email_thread_id,
        ),
    )
    if commit:
        conn.commit()


def get_enquiry_draft(email_thread_id: int, conn: sqlite3.Connection) -> dict | None:
    """The shared draft as the UI needs it: subject, raw template, parsed fields,
    edit mode, and the rendered `body` ({{field}}s filled, {{supplier_name}} left
    as a visible placeholder)."""
    row = conn.execute(
        "SELECT draft_subject, draft_body_template, draft_fields_json, draft_edit_mode "
        "FROM email_threads WHERE id = ?",
        (email_thread_id,),
    ).fetchone()
    if row is None or row["draft_body_template"] is None:
        return None
    try:
        fields = json.loads(row["draft_fields_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        fields = []
    template = row["draft_body_template"] or ""
    mode = row["draft_edit_mode"] or "fields"
    body = template if mode == "free_text" else render_body(template, fields)
    return {
        "subject": row["draft_subject"] or "",
        "body_template": template,
        "fields": fields,
        "edit_mode": mode,
        "body": body,
    }


_ENQUIRY_DRAFT_COLS = {
    "draft_subject": "draft_subject",
    "draft_body": "draft_body_template",  # free-text body is stored as the template
    "draft_edit_mode": "draft_edit_mode",
}


def apply_enquiry_draft_edits(
    email_thread_id: int, edits: dict, conn: sqlite3.Connection
) -> dict | None:
    """Persist operator edits to the shared draft. Keys: draft_subject,
    draft_body (free-text mode — stored as the template verbatim), draft_edit_mode,
    fields (list). Mirrors apply_draft_edits but on the email_threads row."""
    current = get_enquiry_draft(email_thread_id, conn)
    if current is None:
        return None

    sets: list[str] = []
    params: list = []
    for key, col in _ENQUIRY_DRAFT_COLS.items():
        if key in edits and edits[key] is not None:
            sets.append(f"{col} = ?")
            params.append(edits[key])

    if edits.get("fields") is not None:
        sets.append("draft_fields_json = ?")
        params.append(json.dumps(edits["fields"]))

    if not sets:
        return current
    conn.execute(
        f"UPDATE email_threads SET {', '.join(sets)} WHERE id = ?",
        [*params, email_thread_id],
    )
    conn.commit()
    return get_enquiry_draft(email_thread_id, conn)


def render_for_supplier(template: str, fields: list[dict], supplier_name: str) -> str:
    """Shared template -> the concrete text one supplier receives: {{supplier_name}}
    filled in, then every {{field}} rendered."""
    return render_body(substitute(template, {"supplier_name": supplier_name or "team"}), fields)


def send_enquiry_to_suppliers(
    email_thread_id: int, supplier_ids: list[int], conn: sqlite3.Connection
) -> dict:
    """Mark the shared draft sent to each selected supplier (mark-only — no
    gmail.send). Snapshots the exact per-supplier text onto that supplier's
    supplier_quote_requests row. Returns {sent, total, newly_sent:[...]}. """
    draft = get_enquiry_draft(email_thread_id, conn)
    rows = conn.execute(
        """
        SELECT sqr.id, sqr.supplier_id, sqr.status, s.name AS supplier_name
        FROM supplier_quote_requests sqr JOIN suppliers s ON s.id = sqr.supplier_id
        WHERE sqr.email_thread_id = ?
        """,
        (email_thread_id,),
    ).fetchall()
    want = set(supplier_ids)
    newly_sent: list[int] = []
    for r in rows:
        if r["supplier_id"] not in want or r["status"] == "sent":
            continue
        body = (
            render_for_supplier(draft["body_template"], draft["fields"], r["supplier_name"])
            if draft
            else None
        )
        if body is not None:
            conn.execute(
                "UPDATE supplier_quote_requests SET draft_subject = ?, draft_body = ?, "
                "status = 'sent', sent_at = ? WHERE id = ?",
                (draft["subject"], body, _now(), r["id"]),
            )
        else:
            conn.execute(
                "UPDATE supplier_quote_requests SET status = 'sent', sent_at = ? WHERE id = ?",
                (_now(), r["id"]),
            )
        newly_sent.append(r["supplier_id"])
    conn.commit()
    progress = enquiry_progress(email_thread_id, conn)
    return {**progress, "newly_sent": newly_sent}


def finalize_thread_status(
    email_thread_id: int, status: str, conn: sqlite3.Connection, *, commit: bool = True
) -> None:
    """Land the enquiry on a terminal status ('drafted' or 'logged').

    'drafted' also pins pipeline_stage to 'drafted'; 'logged' leaves the stage
    it reached (typically 'extracted') so the trace still reads truthfully.
    """
    if status == "drafted":
        conn.execute(
            "UPDATE email_threads SET status = 'drafted', pipeline_stage = 'drafted', "
            "pipeline_updated_at = ? WHERE id = ?",
            (_now(), email_thread_id),
        )
    else:
        conn.execute(
            "UPDATE email_threads SET status = ?, pipeline_updated_at = ? WHERE id = ?",
            (status, _now(), email_thread_id),
        )
    if commit:
        conn.commit()


def delete_incomplete_thread(email_thread_id: int, conn: sqlite3.Connection) -> None:
    """Drop a half-processed inbound thread and any drafts it produced.

    Called when a redelivery finds a prior row that never reached a terminal
    status (the first attempt crashed mid-pipeline, e.g. an LLM timeout). Wiping
    it lets the retry reprocess from a clean slate instead of the idempotency
    gate falsely reporting the enquiry as finished.
    """
    conn.execute(
        "DELETE FROM supplier_quote_requests WHERE email_thread_id = ?", (email_thread_id,)
    )
    conn.execute("DELETE FROM email_threads WHERE id = ?", (email_thread_id,))
    conn.commit()
    logger.info("deleted incomplete email_thread id=%s (and its draft rows) for reprocessing",
                email_thread_id)


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


_EDITABLE_COLS = {"draft_to", "draft_subject", "draft_body", "draft_edit_mode"}


def apply_draft_edits(sqr_id: int, edits: dict, conn: sqlite3.Connection) -> dict | None:
    """Persist operator edits to one SQR. Keys: draft_to/draft_subject/draft_body/
    draft_edit_mode, plus `fields` (list). When `fields` change and the row is in
    'fields' mode and no explicit body was supplied, `draft_body` is re-rendered
    from `draft_body_template` so the canonical body never drifts from the fields.
    """
    from gmail_trigger_poc.llm import render_body

    current = get_supplier_draft(sqr_id, conn)
    if current is None:
        return None

    sets: list[str] = []
    params: list = []
    for col in _EDITABLE_COLS:
        if col in edits and edits[col] is not None:
            sets.append(f"{col} = ?")
            params.append(edits[col])

    new_fields = edits.get("fields")
    if new_fields is not None:
        sets.append("draft_fields_json = ?")
        params.append(json.dumps(new_fields))
        mode = edits.get("draft_edit_mode") or current["draft_edit_mode"]
        if "draft_body" not in edits and mode == "fields":
            sets.append("draft_body = ?")
            params.append(render_body(current["draft_body_template"], new_fields))

    if not sets:
        return current
    conn.execute(
        f"UPDATE supplier_quote_requests SET {', '.join(sets)} WHERE id = ?",
        [*params, sqr_id],
    )
    conn.commit()
    return get_supplier_draft(sqr_id, conn)


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


def build_thread_trace(
    thread_row,
    sent_progress: dict,
    draft_prog: dict | None = None,
    matched_names: list[str] | None = None,
) -> list[dict]:
    """The processing trace for the detail pane (plans/v2SQ-quoteflow-ui.html).

    5 nodes: received -> extracted -> suppliers matched -> drafts generated
    (per-supplier, live) -> all sent. A node is `done` once the enquiry has
    reached at least that stage; the drafting node also carries "N of M" while
    it's in flight. `matched_names`, when given, names the suppliers on the
    "matched" node instead of just counting them.
    """
    stage = (thread_row["pipeline_stage"] if "pipeline_stage" in thread_row.keys() else None)
    status = (thread_row["status"] if "status" in thread_row.keys() else None)
    reached = PIPELINE_STAGES.index(stage) if stage in PIPELINE_STAGES else -1
    if status == "drafted":
        reached = max(reached, PIPELINE_STAGES.index("drafted"))

    draft_prog = draft_prog or {"done": 0, "total": 0}
    d_done, d_total = draft_prog.get("done", 0), draft_prog.get("total", 0)
    s_total = sent_progress.get("total", 0)
    s_sent = sent_progress.get("sent", 0)

    i_matched = PIPELINE_STAGES.index("matched")
    i_drafting = PIPELINE_STAGES.index("drafting")
    i_drafted = PIPELINE_STAGES.index("drafted")

    trace = [
        {"key": "received", "label": "Enquiry received", "done": reached >= 0,
         "detail": (f"Received {thread_row['processed_at']}"
                    if thread_row["processed_at"] else "Received") if reached >= 0 else None},
        {"key": "extracted", "label": "Content extracted", "done": reached >= 1,
         "detail": "Product, quantity and delivery window parsed" if reached >= 1 else None},
        {"key": "matched", "label": "Suppliers matched", "done": reached >= i_matched,
         "detail": (
             (", ".join(matched_names) if matched_names
              else f"{d_total} supplier(s) cleared the match threshold"
              if d_total else "Matching suppliers")
         ) if reached >= i_matched else None},
    ]

    # The drafting node: pending -> in-progress ("2 of 3") -> done.
    if reached >= i_drafted or (d_total and d_done >= d_total):
        drafting = {"done": True, "detail": f"Outreach drafted for all {d_total or d_done} supplier(s)"}
    elif reached >= i_drafting or (reached >= i_matched and d_done):
        drafting = {"done": False, "detail": f"Drafting {d_done} of {d_total} — in progress"}
    else:
        drafting = {"done": False, "detail": None}
    trace.append({"key": "generated", "label": "Supplier drafts generated", **drafting})

    trace.append({
        "key": "all_sent",
        "label": "All supplier drafts sent",
        "done": s_total > 0 and s_sent == s_total,
        "detail": f"{s_sent} of {s_total} sent" if s_total else None,
    })
    return trace
