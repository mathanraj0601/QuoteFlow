"""process_email() — the one shared entry point into the processing pipeline.

Called two ways (see §9.1 of the integration guide):
  1. Directly, in-process, by the background poller.
  2. Via `POST /process-email`, a thin FastAPI wrapper, for curl/Postman testing
     and any future non-poller trigger.

Step 2 (classify -> extract -> match -> draft) is NOT built yet. This stub just
echoes the email back so Step 1 plumbing can be validated end to end.
"""

from __future__ import annotations

import logging
from typing import Any

from gmail_trigger_poc.config import get_settings
from gmail_trigger_poc.logging_setup import body_block, preview

logger = logging.getLogger(__name__)


def process_email(email_data: dict[str, Any]) -> dict[str, Any]:
    """The actual pipeline. Step 2 logic goes here.

    Must be safe to run twice on the same email (at-least-once delivery: the
    poller only marks a message read after this returns without raising).
    """
    settings = get_settings()
    body = email_data.get("body") or ""
    body_chars = len(body)
    logger.info(
        "process_email: id=%s thread=%s from=%r subject=%r body_chars=%d",
        email_data.get("id"), email_data.get("thread_id"),
        email_data.get("from"), email_data.get("subject"), body_chars,
    )
    logger.info("     body   : %s", preview(body, settings.log_body_preview_chars))
    if settings.log_full_body and body:
        logger.debug("process_email full body:%s", body_block(body))

    # --- Step 2 will go here -------------------------------------------------
    # logger.debug("classify -> ...");  logger.debug("extract -> ...")
    # logger.debug("match -> ...");     logger.debug("draft -> ...")
    # ----------------------------------------------------------------------

    result = {
        "status": "received",
        "step": 1,
        "message_id": email_data.get("id"),
        "thread_id": email_data.get("thread_id"),
        "from": email_data.get("from"),
        "subject": email_data.get("subject"),
        "body_chars": body_chars,
        "quotation_id": None,
    }
    logger.debug("process_email result: %s", result)
    return result
