"""Thin helpers over the Gmail API: history checkpoint, message fetch, mark-read."""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC
from typing import Any

from gmail_trigger_poc.config import Settings, get_settings
from gmail_trigger_poc.logging_setup import body_block, preview

logger = logging.getLogger(__name__)


def _internal_date_iso(msg: dict[str, Any]) -> str | None:
    raw = msg.get("internalDate")
    if not raw:
        return None
    try:
        from datetime import datetime, timezone

        return (
            datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
            .isoformat(timespec="seconds")
        )
    except (ValueError, OverflowError):
        return raw


def get_start_history_id(service, settings: Settings | None = None) -> str:
    """Read the saved checkpoint, or baseline from the current profile (start now)."""
    settings = settings or get_settings()
    if os.path.exists(settings.history_file):
        with open(settings.history_file, encoding="utf-8") as f:
            hid = json.load(f)["historyId"]
        logger.debug("loaded history checkpoint from %s: %s", settings.history_file, hid)
        return hid
    profile = service.users().getProfile(userId=settings.user_id).execute()
    logger.info(
        "no checkpoint file -> baselining from current profile historyId=%s "
        "(only mail arriving from now on will be seen)",
        profile["historyId"],
    )
    return profile["historyId"]


def save_history_id(history_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    with open(settings.history_file, "w", encoding="utf-8") as f:
        json.dump({"historyId": history_id}, f)
    logger.debug("saved history checkpoint: %s -> %s", history_id, settings.history_file)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _extract_plain_text(payload: dict[str, Any], _depth: int = 0) -> str:
    """Walk the MIME tree and return the first text/plain part.

    Falls back to the top-level body only for a non-multipart message that
    carries no explicit mimeType (never for an HTML-only part).
    """
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    parts = payload.get("parts") or []
    for part in parts:
        text = _extract_plain_text(part, _depth + 1)
        if text:
            return text
    if (
        _depth == 0
        and not parts
        and not payload.get("mimeType")
        and payload.get("body", {}).get("data")
    ):
        return _decode(payload["body"]["data"])
    return ""


def get_message_body(service, msg_id: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    logger.debug("messages.get id=%s format=full", msg_id)
    msg = (
        service.users()
        .messages()
        .get(userId=settings.user_id, id=msg_id, format="full")
        .execute()
    )
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    body = _extract_plain_text(payload)
    labels = msg.get("labelIds", [])
    data = {
        "id": msg_id,
        "thread_id": msg.get("threadId"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
        "received": _internal_date_iso(msg),
        "labels": labels,
        "snippet": msg.get("snippet"),
        "body": body,
    }
    logger.debug(
        "parsed message id=%s thread=%s mime=%s labels=%s received=%s size=%s body_chars=%d",
        msg_id, data["thread_id"], payload.get("mimeType"), labels,
        data["received"], msg.get("sizeEstimate"), len(body or ""),
    )
    logger.info("     from   : %s", data["from"])
    logger.info("     to     : %s", data["to"])
    logger.info("     subject: %s", data["subject"])
    logger.info("     snippet: %s", preview(data["snippet"], settings.log_body_preview_chars))
    logger.info("     body   : %s", preview(body, settings.log_body_preview_chars))
    if settings.log_full_body:
        logger.debug("full body id=%s (%d lines):%s",
                     msg_id, len((body or "").splitlines()) or 1, body_block(body))
    return data


def mark_as_read(service, msg_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    updated = (
        service.users()
        .messages()
        .modify(userId=settings.user_id, id=msg_id, body={"removeLabelIds": ["UNREAD"]})
        .execute()
    )
    labels = updated.get("labelIds", [])
    still_unread = "UNREAD" in labels
    logger.info(
        "marked id=%s as read (labels now: %s)%s",
        msg_id, labels, "  <-- WARNING: still UNREAD" if still_unread else "",
    )


def list_new_message_ids(service, start_history_id: str, settings: Settings | None = None):
    """Yield (message_id, new_history_id) for messagesAdded since start_history_id.

    Raises googleapiclient.errors.HttpError with status 404 if the checkpoint is
    too old (Gmail retains history records ~1 week) — caller should re-baseline.
    """
    settings = settings or get_settings()
    page_token = None
    latest_history_id = start_history_id
    page_no = 0
    total_msgs = 0
    while True:
        page_no += 1
        logger.debug(
            "history.list startHistoryId=%s page=%d types=[messageAdded]",
            start_history_id, page_no,
        )
        resp = (
            service.users()
            .history()
            .list(
                userId=settings.user_id,
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                pageToken=page_token,
            )
            .execute()
        )
        records = resp.get("history", [])
        logger.debug(
            "history.list page=%d -> %d record(s) historyId=%s nextPageToken=%s",
            page_no, len(records), resp.get("historyId"), bool(resp.get("nextPageToken")),
        )
        for record in records:
            for msg_added in record.get("messagesAdded", []):
                total_msgs += 1
                m = msg_added["message"]
                logger.debug(
                    "  messageAdded id=%s thread=%s labels=%s",
                    m.get("id"), m.get("threadId"), m.get("labelIds", []),
                )
                yield m["id"], resp.get("historyId", latest_history_id)
        latest_history_id = resp.get("historyId", latest_history_id)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    if total_msgs:
        logger.debug("history scan done: %d new message(s), latest historyId=%s",
                     total_msgs, latest_history_id)
    # sentinel: final history id even when there were no messages
    yield None, latest_history_id
