"""Background polling loop — history-based (users.history.list, 2 quota units/call)."""

from __future__ import annotations

import logging
import time

from googleapiclient.errors import HttpError

from gmail_trigger_poc.auth import get_gmail_service
from gmail_trigger_poc.config import Settings, get_settings
from gmail_trigger_poc.gmail_client import (
    get_message_body,
    get_start_history_id,
    list_new_message_ids,
    mark_as_read,
    save_history_id,
)
from gmail_trigger_poc.pipeline import process_email

logger = logging.getLogger(__name__)


def _rebaseline(service, settings: Settings) -> str:
    profile = service.users().getProfile(userId=settings.user_id).execute()
    logger.warning("re-baselined to current historyId=%s", profile["historyId"])
    save_history_id(profile["historyId"], settings)
    return profile["historyId"]


def poll_once(service, start_history_id: str, settings: Settings) -> str:
    """One poll cycle. Returns the history id to checkpoint next.

    The checkpoint only advances when *every* new message this cycle was
    processed without raising. If any failed, the checkpoint is held at
    ``start_history_id`` so the whole delta is re-scanned next cycle — that is
    how a failed message gets retried (history-based polling will not re-surface
    it once the checkpoint passes its historyId). ``process_email()`` is required
    to be idempotent, so reprocessing the ones that already succeeded is safe.
    """
    logger.debug("poll cycle start (since historyId=%s)", start_history_id)
    latest_history_id = start_history_id
    seen = 0
    processed = 0
    failed = 0
    try:
        for msg_id, hist_id in list_new_message_ids(service, start_history_id, settings):
            latest_history_id = hist_id or latest_history_id
            if msg_id is None:
                continue
            seen += 1
            logger.info("=" * 60)
            logger.info("NEW EMAIL #%d  id=%s  (fetching full message)", seen, msg_id)
            email_data = get_message_body(service, msg_id, settings)
            try:
                result = process_email(email_data)  # direct in-process call — Step 2 hook
                logger.info("process_email(id=%s) status=%s -> %s",
                            msg_id, result.get("status"), result)
                mark_as_read(service, msg_id, settings)  # only on success
                processed += 1
            except Exception:  # noqa: BLE001 - retry next cycle, keep poller alive
                failed += 1
                logger.warning(
                    "processing failed for id=%s -> left UNREAD, will retry next poll",
                    msg_id, exc_info=True,
                )
    except HttpError as e:
        if e.resp.status == 404:
            logger.warning("historyId %s too old (Gmail keeps ~1 week) -> re-baselining",
                           start_history_id)
            return _rebaseline(service, settings)
        logger.error("history.list failed (HTTP %s) — keeping checkpoint %s",
                     e.resp.status, start_history_id, exc_info=True)
        return start_history_id

    checkpoint = start_history_id if failed else latest_history_id
    save_history_id(checkpoint, settings)
    if failed:
        logger.warning(
            "poll cycle done: %d new / %d processed / %d FAILED -> "
            "checkpoint held at historyId=%s (delta re-scanned next cycle)",
            seen, processed, failed, checkpoint,
        )
    elif seen:
        logger.info("poll cycle done: %d new / %d processed / checkpoint historyId=%s",
                    seen, processed, checkpoint)
    else:
        logger.debug("poll cycle done: no new mail / checkpoint historyId=%s", checkpoint)
    return checkpoint


def poll_gmail(settings: Settings | None = None, stop_event=None) -> None:
    """Blocking loop. Runs on a daemon thread from the FastAPI lifespan."""
    settings = settings or get_settings()
    logger.info("poller: authenticating (user_id=%s)", settings.user_id)
    try:
        service = get_gmail_service(settings)
    except FileNotFoundError as e:
        logger.error("poller not starting: %s", e)
        return
    except Exception:  # noqa: BLE001 - surface auth failure cleanly, keep API up
        logger.exception("poller not starting: authentication failed")
        return

    try:
        profile = service.users().getProfile(userId=settings.user_id).execute()
        logger.info(
            "connected as %s  (%s messages, %s threads, historyId=%s)",
            profile.get("emailAddress"), profile.get("messagesTotal"),
            profile.get("threadsTotal"), profile.get("historyId"),
        )
    except Exception:  # noqa: BLE001 - non-fatal, just a nicety
        logger.warning("could not fetch profile for the 'connected as' line", exc_info=True)

    history_id = get_start_history_id(service, settings)
    logger.info(
        "poller started (interval=%ss, baseline historyId=%s) - waiting for new mail",
        settings.poll_interval_seconds, history_id,
    )

    cycle = 0
    idle_cycles = 0
    while stop_event is None or not stop_event.is_set():
        cycle += 1
        try:
            new_history_id = poll_once(service, history_id, settings)
            if new_history_id == history_id:
                idle_cycles += 1
                hb = settings.log_heartbeat_every
                if hb and idle_cycles % hb == 0:
                    logger.info(
                        "still polling - %d cycles, %d idle, historyId=%s",
                        cycle, idle_cycles, history_id,
                    )
            else:
                idle_cycles = 0
            history_id = new_history_id
        except Exception:  # noqa: BLE001 - never let one bad cycle kill the loop
            logger.exception("unexpected error in poll cycle %d - continuing", cycle)
        if stop_event is not None:
            stop_event.wait(settings.poll_interval_seconds)
        else:
            time.sleep(settings.poll_interval_seconds)

    logger.info("poller stopped (stop_event set) after %d cycle(s)", cycle)
