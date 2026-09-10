"""process_email() — the one shared entry point into the QuoteFlow pipeline.

Called two ways (integration guide §9.1):
  1. Directly, in-process, by the background poller.
  2. Via `POST /process-email`, a thin FastAPI wrapper.

Flow (Plan B §5, v2SQ):
    classify + extract  (LLM)
      -> not a client_enquiry?  log the thread, return {"status": "logged"}
    get_or_create_client / insert inbound email_threads row
    vector match products -> SQL join suppliers -> rerank
    keep every supplier with match_score >= MATCH_SCORE_THRESHOLD
    draft one outreach email per kept supplier  (LLM)  -> supplier_quote_requests
    return {"status": "drafted", "supplier_quote_request_ids": [...], ...}

Idempotent: the poller only marks a Gmail message read after this returns without
raising, so a redelivered message must not create duplicate rows. We key on
`email_threads.gmail_message_id` (UNIQUE) and replay the prior result.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from gmail_trigger_poc.config import Settings, get_settings
from gmail_trigger_poc.db import connect
from gmail_trigger_poc.llm import get_provider
from gmail_trigger_poc.logging_setup import body_block, preview
from gmail_trigger_poc.matching import find_matches
from gmail_trigger_poc.repository import (
    bare_email,
    create_supplier_quote_request,
    find_thread_by_message_id,
    get_or_create_client,
    insert_inbound_thread,
    list_supplier_drafts,
)

logger = logging.getLogger(__name__)

_MATCHED_SUPPLIER_KEYS = (
    "supplier_id", "supplier_name", "supplier_email", "supplier_specialization",
    "product_id", "product_name", "product_category", "match_score",
    "semantic_similarity", "match_source", "unit_price", "lead_time_days",
)


def _matched_view(candidate: dict) -> dict:
    return {k: candidate.get(k) for k in _MATCHED_SUPPLIER_KEYS}


def _replayed_result(thread_row, conn) -> dict | None:
    """If this message was already processed, rebuild its result instead of redoing it."""
    drafts = list_supplier_drafts(thread_row["id"], conn)
    if drafts:
        return {
            "status": "drafted",
            "idempotent_replay": True,
            "email_thread_id": thread_row["id"],
            "thread_id": thread_row["id"],
            "supplier_quote_request_ids": [d["id"] for d in drafts],
            "matched_suppliers": [
                {"supplier_id": d["supplier_id"], "supplier_name": d["supplier_name"],
                 "match_score": d["match_score"]}
                for d in drafts
            ],
        }
    # thread row exists but produced no drafts -> it was a non-enquiry we logged
    try:
        scenario = (json.loads(thread_row["extracted_data_json"] or "{}") or {}).get(
            "scenario", "other"
        )
    except (json.JSONDecodeError, TypeError, IndexError):
        scenario = "other"
    return {
        "status": "logged",
        "idempotent_replay": True,
        "email_thread_id": thread_row["id"],
        "scenario": scenario,
    }


def process_email(email_data: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    conn = connect(settings)
    try:
        return _process(email_data, settings, conn)
    finally:
        conn.close()


def _process(email_data: dict[str, Any], settings: Settings, conn) -> dict[str, Any]:
    msg_id = email_data.get("id")
    body = email_data.get("body") or ""
    logger.info(
        "process_email: id=%s thread=%s from=%r subject=%r body_chars=%d",
        msg_id, email_data.get("thread_id"), email_data.get("from"),
        email_data.get("subject"), len(body),
    )
    logger.info("     body   : %s", preview(body, settings.log_body_preview_chars))
    if settings.log_full_body and body:
        logger.debug("process_email full body:%s", body_block(body))

    # --- idempotency: has this exact Gmail message already been processed? -----
    prior = find_thread_by_message_id(msg_id, conn)
    if prior is not None:
        replay = _replayed_result(prior, conn)
        logger.info("process_email: id=%s already processed -> replaying %s",
                    msg_id, replay["status"])
        return replay

    provider = get_provider(settings)
    extraction = provider.classify_and_extract(email_data)
    logger.info(
        "classify+extract: scenario=%s product=%r qty=%s uom=%s incoterm=%s urgency=%s conf=%.2f",
        extraction.scenario, extraction.product_description, extraction.quantity,
        extraction.unit_of_measure, extraction.delivery_terms, extraction.urgency,
        extraction.confidence,
    )

    if extraction.scenario != "client_enquiry":
        thread_row_id = insert_inbound_thread(
            email_data, extraction, conn, broker_email=settings.broker_email
        )
        conn.commit()
        logger.info("process_email: id=%s not a client enquiry (%s) -> logged only",
                    msg_id, extraction.scenario)
        return {
            "status": "logged",
            "scenario": extraction.scenario,
            "email_thread_id": thread_row_id,
        }

    client_id = get_or_create_client(extraction, email_data.get("from") or "", conn)
    thread_row_id = insert_inbound_thread(
        email_data, extraction, conn, broker_email=settings.broker_email
    )

    query_text = extraction.product_description or (email_data.get("subject") or "")
    ranked = find_matches(query_text, conn, settings)
    threshold = settings.match_score_threshold
    above = [c for c in ranked if c["match_score"] >= threshold]
    logger.info(
        "match: %d candidate(s), %d >= threshold %.2f -> drafting",
        len(ranked), len(above), threshold,
    )
    if not above:
        conn.commit()
        logger.warning(
            "process_email: id=%s no supplier >= %.2f (top=%.3f) -> drafted with 0 outreach",
            msg_id, threshold, ranked[0]["match_score"] if ranked else 0.0,
        )
        return {
            "status": "drafted",
            "email_thread_id": thread_row_id,
            "thread_id": thread_row_id,
            "supplier_quote_request_ids": [],
            "matched_suppliers": [_matched_view(c) for c in ranked[:5]],
            "scenario": extraction.scenario,
        }

    draft_ids: list[int] = []
    for candidate in above:
        draft = provider.draft_supplier_outreach(extraction, candidate, settings)
        if not draft.get("to"):
            draft["to"] = candidate.get("supplier_email") or ""
        sqr_id = create_supplier_quote_request(
            email_thread_id=thread_row_id,
            client_id=client_id,
            candidate=candidate,
            draft=draft,
            extraction=extraction,
            match_score=candidate["match_score"],
            conn=conn,
        )
        draft_ids.append(sqr_id)
        logger.info(
            "  drafted SQR id=%s -> supplier %s <%s> (score=%.3f)",
            sqr_id, candidate.get("supplier_name"), draft["to"], candidate["match_score"],
        )

    conn.commit()
    result = {
        "status": "drafted",
        "email_thread_id": thread_row_id,
        "thread_id": thread_row_id,
        "supplier_quote_request_ids": draft_ids,
        "matched_suppliers": [_matched_view(c) for c in above],
        "scenario": extraction.scenario,
        "client_id": client_id,
    }
    logger.info(
        "process_email: id=%s -> drafted %d supplier outreach email(s) for thread %s",
        msg_id, len(draft_ids), thread_row_id,
    )
    return result


__all__ = ["process_email", "bare_email"]
