"""Single FastAPI process: HTTP API + background Gmail poller thread.

Run:  uvicorn gmail_trigger_poc.app:app --reload
 or:  gmail-trigger-poc            (console script -> run())
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager

from fastapi import Body, FastAPI, HTTPException, Request

from gmail_trigger_poc import repository
from gmail_trigger_poc.config import get_settings
from gmail_trigger_poc.db import connect
from gmail_trigger_poc.logging_setup import configure_logging, preview
from gmail_trigger_poc.pipeline import process_email
from gmail_trigger_poc.poller import poll_gmail

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_poller_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _poller_thread
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting gmail-trigger-poc (log_level=%s, poll_interval=%ss, poller=%s)",
        settings.log_level, settings.poll_interval_seconds, settings.enable_poller,
    )

    if settings.enable_poller:
        _stop_event.clear()
        _poller_thread = threading.Thread(
            target=poll_gmail,
            kwargs={"settings": settings, "stop_event": _stop_event},
            daemon=True,
            name="gmail-poller",
        )
        _poller_thread.start()
        logger.info("poller thread launched: %s", _poller_thread.name)
    else:
        logger.warning("ENABLE_POLLER=false — poller thread NOT started")

    try:
        yield
    finally:
        logger.info("shutdown — signalling poller to stop")
        _stop_event.set()


app = FastAPI(title="gmail-trigger-poc", lifespan=lifespan)


@contextmanager
def _db():
    conn = connect(get_settings())
    try:
        yield conn
    finally:
        conn.close()


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    start = time.perf_counter()
    logger.debug("--> %s %s", request.method, request.url.path)
    response = await call_next(request)
    dur_ms = (time.perf_counter() - start) * 1000
    logger.info("<-- %s %s %d (%.1f ms)",
                request.method, request.url.path, response.status_code, dur_ms)
    return response


@app.get("/health")
def health() -> dict:
    s = get_settings()
    alive = bool(_poller_thread and _poller_thread.is_alive())
    has_token = os.path.exists(s.token_file)
    has_creds = os.path.exists(s.credentials_file)
    logger.debug(
        "health: poller_enabled=%s poller_alive=%s token=%s credentials=%s",
        s.enable_poller, alive, has_token, has_creds,
    )
    has_db = os.path.exists(s.db_file)
    has_chroma = os.path.isdir(s.chroma_path)
    return {
        "status": "ok",
        "poller_enabled": s.enable_poller,
        "poller_alive": alive,
        "poll_interval_seconds": s.poll_interval_seconds,
        "scopes": s.scopes,
        "has_token": has_token,
        "has_credentials": has_creds,
        "has_db": has_db,
        "has_chroma": has_chroma,
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model,
        "embedding_provider": s.embedding_provider,
        "match_score_threshold": s.match_score_threshold,
        "log_level": s.log_level,
    }


@app.post("/process-email")
def process_email_endpoint(gmail_message: dict) -> dict:
    """Thin wrapper around the same method the poller calls (Plan B §7)."""
    s = get_settings()
    logger.info("POST /process-email (manual trigger) keys=%s",
                sorted(gmail_message.keys()))
    logger.info("     subject: %s", gmail_message.get("subject"))
    logger.info("     body   : %s",
                preview(gmail_message.get("body"), s.log_body_preview_chars))
    result = process_email(gmail_message)
    logger.info("     result : status=%s ids=%s",
                result.get("status"), result.get("supplier_quote_request_ids"))
    return result  # noqa: RET504 - explicit for the log line above


def _thread_row(thread_id: int, conn) -> dict:
    row = conn.execute(
        "SELECT * FROM email_threads WHERE id = ?", (thread_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no email thread {thread_id}")
    return dict(row)


@app.get("/enquiries")
def list_enquiries() -> dict:
    """The Desk: inbound client emails with their supplier-outreach progress."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT et.id, et.subject, et.from_email, et.processed_at,
                   COUNT(sqr.id)                                        AS drafts,
                   SUM(CASE WHEN sqr.status='sent' THEN 1 ELSE 0 END)   AS sent
            FROM email_threads et
            LEFT JOIN supplier_quote_requests sqr ON sqr.email_thread_id = et.id
            WHERE et.direction = 'inbound'
            GROUP BY et.id
            ORDER BY et.processed_at DESC, et.id DESC
            """
        ).fetchall()
    enquiries = []
    for r in rows:
        drafts = r["drafts"] or 0
        sent = r["sent"] or 0
        enquiries.append({
            "email_thread_id": r["id"],
            "subject": r["subject"],
            "from_email": r["from_email"],
            "processed_at": r["processed_at"],
            "drafts": drafts,
            "sent": sent,
            "status": "drafted" if drafts else "logged",
            "progress": f"{sent}/{drafts}" if drafts else "-",
        })
    return {"count": len(enquiries), "enquiries": enquiries}


@app.get("/enquiry/{thread_id}/supplier-drafts")
def enquiry_supplier_drafts(thread_id: int) -> dict:
    """Everything the Supplier Outreach Builder needs for one enquiry (Plan B §6)."""
    with _db() as conn:
        thread = _thread_row(thread_id, conn)
        drafts = repository.list_supplier_drafts(thread_id, conn)
        progress = repository.enquiry_progress(thread_id, conn)
    return {
        "email_thread_id": thread_id,
        "progress": progress,
        "original_email": {
            "from": thread["from_email"],
            "subject": thread["subject"],
            "body": thread["body_text"],
            "extracted": json.loads(thread.get("extracted_data_json") or "null"),
        },
        "supplier_drafts": drafts,
    }


@app.patch("/supplier-quote-requests/{sqr_id}")
def update_supplier_draft(sqr_id: int, patch: dict = Body(default_factory=dict)) -> dict:
    """Persist operator edits (to / subject / body / fields) without sending."""
    allowed = {"draft_to", "draft_subject", "draft_body", "draft_edit_mode"}
    sets, params = [], []
    for key in allowed:
        if key in patch:
            sets.append(f"{key} = ?")
            params.append(patch[key])
    if "fields" in patch:
        sets.append("draft_fields_json = ?")
        params.append(json.dumps(patch["fields"]))
    if not sets:
        raise HTTPException(status_code=400, detail="no updatable fields in body")
    with _db() as conn:
        if repository.get_supplier_draft(sqr_id, conn) is None:
            raise HTTPException(status_code=404, detail=f"no supplier draft {sqr_id}")
        conn.execute(
            f"UPDATE supplier_quote_requests SET {', '.join(sets)} WHERE id = ?",
            [*params, sqr_id],
        )
        conn.commit()
        return repository.get_supplier_draft(sqr_id, conn)


@app.post("/supplier-quote-requests/{sqr_id}/send")
def send_supplier_draft(sqr_id: int, payload: dict = Body(default_factory=dict)) -> dict:
    """Mark one supplier draft as sent (mark-only — no gmail.send in this version).

    Optionally accepts the current {to, subject, body, fields} to persist edits
    first, matching the UI's "post current text on send" behaviour (Plan B §6).
    """
    s = get_settings()
    with _db() as conn:
        draft = repository.get_supplier_draft(sqr_id, conn)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"no supplier draft {sqr_id}")
        if draft["status"] == "sent":
            raise HTTPException(status_code=409, detail="already sent")

        updates, params = [], []
        for src, col in (("to", "draft_to"), ("subject", "draft_subject"), ("body", "draft_body")):
            if payload.get(src) is not None:
                updates.append(f"{col} = ?")
                params.append(payload[src])
        if payload.get("fields") is not None:
            updates.append("draft_fields_json = ?")
            params.append(json.dumps(payload["fields"]))
        if updates:
            conn.execute(
                f"UPDATE supplier_quote_requests SET {', '.join(updates)} WHERE id = ?",
                [*params, sqr_id],
            )
            conn.commit()
            draft = repository.get_supplier_draft(sqr_id, conn)

        logger.info(
            "SEND (mark-only) SQR id=%s\n    To: %s\n    Subject: %s\n    Body:\n%s",
            sqr_id, draft["draft_to"], draft["draft_subject"],
            "\n".join("      " + ln for ln in (draft["draft_body"] or "").splitlines()),
        )
        logger.warning(
            "gmail.send is out of scope this version - SQR %s marked sent, not transmitted "
            "(scopes=%s)", sqr_id, s.scopes,
        )
        updated = repository.mark_draft_sent(sqr_id, conn)
        progress = repository.enquiry_progress(updated["email_thread_id"], conn)
    return {"status": "sent", "transmitted": False, "supplier_draft": updated,
            "progress": progress}


@app.get("/suppliers")
def list_suppliers() -> dict:
    """Supplier directory (UI View 3)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, contact_email, region, rating, specialization, "
            "certifications, payment_terms FROM suppliers ORDER BY name"
        ).fetchall()
    return {"count": len(rows), "suppliers": [dict(r) for r in rows]}


def run() -> None:
    import uvicorn

    s = get_settings()
    configure_logging(s.log_level)
    logger.info("uvicorn serving on http://%s:%d", s.host, s.port)
    uvicorn.run("gmail_trigger_poc.app:app", host=s.host, port=s.port, reload=False)


if __name__ == "__main__":
    run()
