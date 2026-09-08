"""Single FastAPI process: HTTP API + background Gmail poller thread.

Run:  uvicorn gmail_trigger_poc.app:app --reload
 or:  gmail-trigger-poc            (console script -> run())
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from gmail_trigger_poc.config import get_settings
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
    return {
        "status": "ok",
        "poller_enabled": s.enable_poller,
        "poller_alive": alive,
        "poll_interval_seconds": s.poll_interval_seconds,
        "scopes": s.scopes,
        "has_token": has_token,
        "has_credentials": has_creds,
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
    logger.info("     result : %s", result)
    return result  # noqa: RET504 - explicit for the log line above


def run() -> None:
    import uvicorn

    s = get_settings()
    configure_logging(s.log_level)
    logger.info("uvicorn serving on http://%s:%d", s.host, s.port)
    uvicorn.run("gmail_trigger_poc.app:app", host=s.host, port=s.port, reload=False)


if __name__ == "__main__":
    run()
