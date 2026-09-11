"""Single FastAPI process: HTTP API + background Gmail poller thread.

Run:  uvicorn gmail_trigger_poc.app:app --reload
 or:  gmail-trigger-poc            (console script -> run())
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gmail_trigger_poc import progress, repository
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

    # Live progress: let the pipeline (running on the poller thread) push events
    # to the WebSocket clients that live on this event loop.
    progress.bind_loop(asyncio.get_running_loop())

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
        progress.unbind_loop()


app = FastAPI(title="gmail-trigger-poc", lifespan=lifespan)


@app.websocket("/ws")
async def progress_ws(ws: WebSocket) -> None:
    """Live pipeline progress. The server pushes `{event, ...}` messages; the
    client sends nothing (we only read to notice a disconnect)."""
    await ws.accept()
    progress.add_client(ws)
    logger.info("ws client connected (%d total)", progress.client_count())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        progress.remove_client(ws)
        logger.info("ws client disconnected (%d left)", progress.client_count())

# Dashboard: the built React app (frontend/dist) if present, else the no-build
# fallback in web/. Both are the same "Supplier Outreach Desk" over the API.
_PKG_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_ROOT.parent.parent
_REACT_DIST = _REPO_ROOT / "frontend" / "dist"
_WEB_FALLBACK = _PKG_ROOT / "web" / "index.html"

if (_REACT_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_REACT_DIST / "assets"), name="assets")

# Favicon + touch-icon: Vite copies frontend/public/* to dist/'s root (not
# dist/assets/), so they need their own routes alongside the /assets mount.
_ROOT_STATIC_FILES = {
    "/favicon.svg": "favicon.svg",
    "/favicon-32.png": "favicon-32.png",
    "/favicon-16.png": "favicon-16.png",
    "/apple-touch-icon.png": "apple-touch-icon.png",
}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """The Supplier Outreach Desk — a single-page dashboard over all the endpoints."""
    index = _REACT_DIST / "index.html"
    return FileResponse(index if index.is_file() else _WEB_FALLBACK)


def _make_root_static_route(filename: str):
    def _serve() -> FileResponse:
        return FileResponse(_REACT_DIST / filename)
    return _serve


for _route_path, _filename in _ROOT_STATIC_FILES.items():
    if (_REACT_DIST / _filename).is_file():
        app.get(_route_path, include_in_schema=False)(_make_root_static_route(_filename))


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
        "llm_model": s.ollama_model if s.llm_provider == "ollama" else s.llm_model,
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


def _thread_row(thread_id: int, conn):
    row = conn.execute(
        "SELECT * FROM email_threads WHERE id = ?", (thread_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no email thread {thread_id}")
    return row


@app.get("/enquiries")
def list_enquiries(include_all: bool = False) -> dict:
    """The Desk: inbound emails the live pipeline has handled + outreach progress.

    By default only rows the live pipeline produced (an extracted `scenario`, or
    at least one supplier draft) are returned — `?include_all=true` also lists the seed
    backfill's inbound rows.
    """
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT et.id, et.subject, et.from_email, et.processed_at,
                   et.extracted_data_json, et.status AS thread_status, et.pipeline_stage,
                   et.drafts_total,
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
        try:
            scenario = (json.loads(r["extracted_data_json"] or "{}") or {}).get("scenario")
        except (json.JSONDecodeError, TypeError):
            scenario = None
        drafts = r["drafts"] or 0
        thread_status = r["thread_status"]
        # Seed-backfill rows carry no lifecycle status and never got a draft — hide
        # them unless asked. Live rows (any status, or ≥1 draft) always show.
        if (
            not include_all
            and drafts == 0
            and scenario is None
            and thread_status in (None, "logged")
        ):
            continue
        sent = r["sent"] or 0
        # Prefer the stored lifecycle status; fall back for pre-migration rows.
        status = thread_status or ("drafted" if drafts else "logged")
        drafts_total = r["drafts_total"] if "drafts_total" in r.keys() and r["drafts_total"] is not None else drafts
        enquiries.append({
            "email_thread_id": r["id"],
            "subject": r["subject"],
            "from_email": r["from_email"],
            "processed_at": r["processed_at"],
            "scenario": scenario,
            "drafts": drafts,
            "drafts_total": max(drafts_total, drafts),
            "sent": sent,
            "status": status,
            "pipeline_stage": r["pipeline_stage"],
            "all_sent": drafts > 0 and sent == drafts,
            "progress": f"{sent}/{drafts}" if drafts else "-",
        })
    return {"count": len(enquiries), "enquiries": enquiries}


def _matched_suppliers_view(thread_id: int, conn) -> list[dict]:
    """Matched suppliers + their per-supplier send state, for the checklist UI."""
    matched = repository.get_matched_suppliers(thread_id, conn)
    records = {
        d["supplier_id"]: d
        for d in repository.list_supplier_drafts(thread_id, conn)
    }
    out = []
    for m in matched:
        rec = records.get(m.get("supplier_id"), {})
        out.append({
            "supplier_id": m.get("supplier_id"),
            "name": m.get("supplier_name") or rec.get("supplier_name"),
            "email": m.get("supplier_email") or rec.get("draft_to"),
            "match_score": m.get("match_score") or rec.get("match_score"),
            "sqr_id": rec.get("id"),
            "status": rec.get("status") or "pending",
            "sent_at": rec.get("sent_at"),
        })
    # fall back to the send records themselves if we have no snapshot (pre-migration row)
    if not out:
        for d in records.values():
            out.append({
                "supplier_id": d["supplier_id"], "name": d["supplier_name"],
                "email": d["draft_to"], "match_score": d["match_score"],
                "sqr_id": d["id"], "status": d["status"], "sent_at": d.get("sent_at"),
            })
    return out


@app.get("/enquiry/{thread_id}/supplier-drafts")
def enquiry_supplier_drafts(thread_id: int) -> dict:
    """Everything the Supplier Outreach Builder needs for one enquiry (Plan B §6)."""
    with _db() as conn:
        thread_row = _thread_row(thread_id, conn)
        thread = dict(thread_row)
        drafts = repository.list_supplier_drafts(thread_id, conn)
        sent_progress = repository.enquiry_progress(thread_id, conn)
        draft_prog = repository.draft_progress(thread_id, conn)
        matched = repository.get_matched_suppliers(thread_id, conn)
        matched_names = [m.get("supplier_name") for m in matched if m.get("supplier_name")]
        trace = repository.build_thread_trace(
            thread_row, sent_progress, draft_prog, matched_names or None
        )
        enquiry_draft = repository.get_enquiry_draft(thread_id, conn)
        matched_suppliers = _matched_suppliers_view(thread_id, conn)
    return {
        "email_thread_id": thread_id,
        "status": thread.get("status") or ("drafted" if drafts else "logged"),
        "pipeline_stage": thread.get("pipeline_stage"),
        "trace": trace,
        "progress": sent_progress,
        "draft_progress": draft_prog,
        "original_email": {
            "from": thread["from_email"],
            "subject": thread["subject"],
            "body": thread["body_text"],
            "extracted": json.loads(thread.get("extracted_data_json") or "null"),
        },
        "enquiry_draft": enquiry_draft,
        "matched_suppliers": matched_suppliers,
        "supplier_drafts": drafts,
    }


@app.patch("/enquiry/{thread_id}/draft")
def update_enquiry_draft(thread_id: int, patch: dict = Body(default_factory=dict)) -> dict:
    """Persist operator edits to the ONE shared outreach draft (subject / body /
    fields / edit mode). Editing `fields` in 'fields' mode re-renders the body
    from the template on read."""
    edits = _validated_edits(patch)
    if not edits:
        raise HTTPException(status_code=400, detail="no updatable fields in body")
    with _db() as conn:
        _thread_row(thread_id, conn)  # 404 if the enquiry doesn't exist
        updated = repository.apply_enquiry_draft_edits(thread_id, edits, conn)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"enquiry {thread_id} has no shared draft yet")
    progress.emit("draft_updated", email_thread_id=thread_id)
    return updated


@app.post("/enquiry/{thread_id}/send")
def send_enquiry(thread_id: int, payload: dict = Body(default_factory=dict)) -> dict:
    """Send the shared draft to the selected matched suppliers in one action
    (mark-only — no gmail.send). `payload = {"supplier_ids": [1, 2, ...]}`."""
    s = get_settings()
    ids = payload.get("supplier_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=400, detail="supplier_ids must be a non-empty list of ints")
    with _db() as conn:
        _thread_row(thread_id, conn)
        if repository.get_enquiry_draft(thread_id, conn) is None:
            raise HTTPException(status_code=409, detail="no shared draft to send for this enquiry")
        result = repository.send_enquiry_to_suppliers(thread_id, ids, conn)
    if not result["newly_sent"]:
        raise HTTPException(
            status_code=409,
            detail="none of the given supplier_ids are unsent matched suppliers for this enquiry",
        )
    logger.info(
        "SEND (mark-only) enquiry #%s -> suppliers %s  (%d/%d sent)  scopes=%s",
        thread_id, result["newly_sent"], result["sent"], result["total"], s.scopes,
    )
    progress.emit("draft_sent", email_thread_id=thread_id,
                  sent=result["sent"], total=result["total"], newly_sent=result["newly_sent"])
    return {"status": "sent", "transmitted": False, **result,
            "progress": {"sent": result["sent"], "total": result["total"]}}


_PATCHABLE = {"draft_to", "draft_subject", "draft_body", "draft_edit_mode", "fields"}


def _validated_edits(payload: dict) -> dict:
    edits = {k: payload[k] for k in _PATCHABLE if k in payload}
    if edits.get("draft_edit_mode") not in (None, "fields", "free_text"):
        raise HTTPException(status_code=400, detail="draft_edit_mode must be 'fields' or 'free_text'")
    if "fields" in edits and not isinstance(edits["fields"], list):
        raise HTTPException(status_code=400, detail="fields must be a list")
    return edits


@app.patch("/supplier-quote-requests/{sqr_id}")
def update_supplier_draft(sqr_id: int, patch: dict = Body(default_factory=dict)) -> dict:
    """Persist operator edits (to / subject / body / fields) without sending.

    Editing `fields` while the row is in 'fields' mode re-renders `draft_body`
    from the template unless an explicit `draft_body` is also supplied.
    """
    edits = _validated_edits(patch)
    if not edits:
        raise HTTPException(status_code=400, detail="no updatable fields in body")
    with _db() as conn:
        updated = repository.apply_draft_edits(sqr_id, edits, conn)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"no supplier draft {sqr_id}")
    progress.emit("draft_updated", email_thread_id=updated["email_thread_id"], sqr_id=sqr_id)
    return updated


@app.post("/supplier-quote-requests/{sqr_id}/send")
def send_supplier_draft(sqr_id: int, payload: dict = Body(default_factory=dict)) -> dict:
    """Mark one supplier draft as sent (mark-only — no gmail.send in this version).

    Optionally accepts the current {to, subject, body, fields} to persist edits
    first, matching the UI's "post current text on send" behaviour (Plan B §6).
    """
    s = get_settings()
    # accept the UI's {to, subject, body, fields} as well as the draft_* column names
    edits = _validated_edits({
        **payload,
        **{f"draft_{k}": payload[k] for k in ("to", "subject", "body") if k in payload},
    })
    with _db() as conn:
        draft = repository.get_supplier_draft(sqr_id, conn)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"no supplier draft {sqr_id}")
        if draft["status"] == "sent":
            raise HTTPException(status_code=409, detail="already sent")

        if edits:
            draft = repository.apply_draft_edits(sqr_id, edits, conn)

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
        sent_progress = repository.enquiry_progress(updated["email_thread_id"], conn)
    progress.emit("draft_sent", email_thread_id=updated["email_thread_id"], sqr_id=sqr_id,
                  sent=sent_progress["sent"], total=sent_progress["total"])
    return {"status": "sent", "transmitted": False, "supplier_draft": updated,
            "progress": sent_progress}


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
