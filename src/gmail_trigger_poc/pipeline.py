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
import time
from typing import Any

from gmail_trigger_poc.config import Settings, get_settings
from gmail_trigger_poc.db import connect
from gmail_trigger_poc.llm import get_provider, substitute
from gmail_trigger_poc.logging_setup import body_block, preview
from gmail_trigger_poc.matching import find_matches
from gmail_trigger_poc import progress
from gmail_trigger_poc.repository import (
    attach_extraction,
    bare_email,
    create_received_thread,
    create_supplier_quote_request,
    delete_incomplete_thread,
    finalize_thread_status,
    find_thread_by_message_id,
    get_or_create_client,
    list_supplier_drafts,
    render_for_supplier,
    set_drafts_total,
    set_enquiry_draft,
    set_matched_suppliers,
    set_pipeline_stage,
)

# Statuses that mean the previous run of this message actually finished. Anything
# else on a redelivered message = a crashed prior attempt -> reprocess it.
_TERMINAL_STATUSES = {"drafted", "logged"}

logger = logging.getLogger(__name__)

_MATCHED_SUPPLIER_KEYS = (
    "supplier_id", "supplier_name", "supplier_email", "supplier_specialization",
    "product_id", "product_name", "product_category", "match_score",
    "semantic_similarity", "match_source", "unit_price", "lead_time_days",
)


def _matched_view(candidate: dict) -> dict:
    return {k: candidate.get(k) for k in _MATCHED_SUPPLIER_KEYS}


def _demo_pause(settings: Settings, after: str) -> None:
    """Optional artificial wait so the Desk's live status is watchable in a demo.

    Off by default (PIPELINE_STEP_DELAY_SECONDS=0). The pipeline commits the
    lifecycle column *before* this sleeps, so a Desk poll during the pause sees
    the new stage.
    """
    delay = settings.pipeline_step_delay_seconds
    if delay > 0:
        logger.info("    zzz  holding %.0fs after %s  (PIPELINE_STEP_DELAY_SECONDS) ...",
                    delay, after)
        time.sleep(delay)


_FLOW_DIAGRAM = r"""
   inbound email
        |
   [STEP 1] receive ..................... REPO insert email_threads (status='new', stage='received')
        |                                 -> Desk shows "Enquiry received" in real time
        v
   [STEP 2] classify + extract .......... LLM  -> scenario / product / qty / incoterm / dest
        |                                 REPO attach extraction (status='processing', 'extracted')
        |                                 (not a client enquiry -> status='logged', stop)
        v
   [STEP 3] match suppliers ............. VDB  Chroma vector search (top-N products)
        |                                 REPO SQL join -> supplier_products + suppliers
        |                                 MATCH rerank -> match_score 0..1, keep >= threshold
        |                                 REPO snapshot matched_suppliers_json, stage='matched'
        v
   [STEP 4] draft + fan out ............ LLM  ONE shared outreach email ({{supplier_name}} token)
        |                                 REPO email_threads.draft_* ; stage='drafting'
        |                                 REPO one supplier_quote_requests send-record per supplier
        v
   [STEP 5] persist + finalize ......... REPO email_threads.status = 'drafted', commit
        |
   result -> {status, email_thread_id, supplier_quote_request_ids[], matched_suppliers[]}
"""


def _log_flow_diagram() -> None:
    for line in _FLOW_DIAGRAM.strip("\n").splitlines():
        logger.info("%s", line)


def _replayed_result(thread_row, conn) -> dict:
    """Rebuild a prior result for an already-processed message (no re-work, no re-draft)."""
    try:
        scenario = (json.loads(thread_row["extracted_data_json"] or "{}") or {}).get(
            "scenario", "other"
        )
    except (json.JSONDecodeError, TypeError):
        scenario = "other"

    if scenario != "client_enquiry":
        return {
            "status": "logged",
            "idempotent_replay": True,
            "email_thread_id": thread_row["id"],
            "scenario": scenario,
        }

    # A crash between "row inserted" and "drafting finished" can leave the row at
    # 'processing'. The replay is the authoritative "it's done" signal, so land
    # the lifecycle columns on 'drafted' here too.
    if thread_row["status"] != "drafted":
        finalize_thread_status(thread_row["id"], "drafted", conn)

    drafts = list_supplier_drafts(thread_row["id"], conn)
    return {
        "status": "drafted",
        "idempotent_replay": True,
        "email_thread_id": thread_row["id"],
        "thread_id": thread_row["id"],
        "scenario": scenario,
        "supplier_quote_request_ids": [d["id"] for d in drafts],
        "matched_suppliers": [
            {"supplier_id": d["supplier_id"], "supplier_name": d["supplier_name"],
             "match_score": d["match_score"]}
            for d in drafts
        ],
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
    logger.info("================ PIPELINE START ================")
    _log_flow_diagram()
    logger.info("-----------------------------------------------")
    logger.info(
        "process_email: id=%s thread=%s from=%r subject=%r body_chars=%d",
        msg_id, email_data.get("thread_id"), email_data.get("from"),
        email_data.get("subject"), len(body),
    )
    logger.info("     body   : %s", preview(body, settings.log_body_preview_chars))
    if settings.log_full_body and body:
        logger.debug("process_email full body:%s", body_block(body))
    if settings.pipeline_step_delay_seconds > 0:
        logger.info("     demo pacing ON: %.0fs pause after each step",
                    settings.pipeline_step_delay_seconds)

    # --- idempotency: has this exact Gmail message already been processed? -----
    logger.info("STEP 0/5 idempotency check  (email_threads.gmail_message_id UNIQUE)")
    prior = find_thread_by_message_id(msg_id, conn)
    if prior is not None:
        prior_status = prior["status"] if "status" in prior.keys() else None
        if prior_status in _TERMINAL_STATUSES:
            replay = _replayed_result(prior, conn)
            logger.info("         -> id=%s already finished (%s); replaying prior result, no re-work",
                        msg_id, prior_status)
            logger.info("================ PIPELINE END (replay) ================")
            return replay
        logger.warning(
            "         -> id=%s has an UNFINISHED prior attempt (status=%s, thread id=%s); "
            "wiping it and reprocessing", msg_id, prior_status, prior["id"],
        )
        delete_incomplete_thread(prior["id"], conn)
    else:
        logger.info("         -> new message, proceeding")

    # STEP 1 — receive: the row (and the Desk's "Enquiry received") exists before any LLM call.
    logger.info("STEP 1/5 receive  (insert email_threads: status='new', stage='received')")
    thread_row_id = create_received_thread(email_data, conn, broker_email=settings.broker_email)
    conn.commit()
    progress.emit("thread", email_thread_id=thread_row_id, status="new",
                  pipeline_stage="received", subject=email_data.get("subject"),
                  from_email=bare_email(email_data.get("from")))
    logger.info("         -> email_thread id=%s created (status=new / received)", thread_row_id)
    _demo_pause(settings, "STEP 1 (row now 'new / received')")

    logger.info("STEP 2/5 classify + extract  (provider=%s)", settings.llm_provider)
    provider = get_provider(settings)
    extraction = provider.classify_and_extract(email_data)
    logger.info("         -> scenario   = %s  (confidence %.2f)",
                extraction.scenario, extraction.confidence)
    logger.info("         -> product    = %r", extraction.product_description)
    logger.info("         -> quantity   = %s %s", extraction.quantity,
                extraction.unit_of_measure or "")
    logger.info("         -> delivery   = %s -> %s",
                extraction.delivery_terms or "(none)", extraction.destination or "(none)")
    logger.info("         -> client     = %s / %s  | urgency=%s",
                extraction.client_name or "(unknown)", extraction.client_company or "(unknown)",
                extraction.urgency)

    if extraction.scenario != "client_enquiry":
        logger.info("         -> not a client enquiry; marking the thread 'logged' and stopping here")
        attach_extraction(thread_row_id, extraction, conn, status="logged",
                          pipeline_stage="extracted")
        progress.emit("thread", email_thread_id=thread_row_id, status="logged",
                      pipeline_stage="extracted", subject=email_data.get("subject"))
        logger.info("================ PIPELINE END: LOGGED (%s), thread %s, no drafts ================",
                    extraction.scenario, thread_row_id)
        return {
            "status": "logged",
            "scenario": extraction.scenario,
            "email_thread_id": thread_row_id,
        }

    logger.info("STEP 2/5 done -> attaching extraction + client (status='processing')")
    client_id = get_or_create_client(extraction, email_data.get("from") or "", conn)
    attach_extraction(thread_row_id, extraction, conn, status="processing",
                      pipeline_stage="extracted")
    progress.emit("thread", email_thread_id=thread_row_id, status="processing",
                  pipeline_stage="extracted", subject=email_data.get("subject"),
                  from_email=bare_email(email_data.get("from")))
    logger.info("         -> client id=%s, email_thread id=%s (status=processing)",
                client_id, thread_row_id)
    _demo_pause(settings, "STEP 2 (row now 'processing / extracted')")

    logger.info("STEP 3/5 match suppliers  (Chroma vector search -> SQL join -> rerank)")
    query_text = extraction.product_description or (email_data.get("subject") or "")
    logger.info("         query text = %r  | top_n=%d  threshold=%.2f",
                query_text, settings.match_top_n, settings.match_score_threshold)
    ranked = find_matches(query_text, conn, settings)
    threshold = settings.match_score_threshold
    above = [c for c in ranked if c["match_score"] >= threshold]
    logger.info("         ranked candidates (score = 0.60*semantic + 0.15*rating + 0.25*category):")
    for i, c in enumerate(ranked, 1):
        logger.info(
            "         %2d. %s %-28s score=%.3f  sem=%.3f  src=%s  unit_price=%s",
            i, "KEEP" if c["match_score"] >= threshold else " -- ",
            c.get("supplier_name"), c["match_score"],
            c.get("semantic_similarity") or 0.0, c.get("match_source"),
            c.get("unit_price"),
        )
    logger.info(
        "         -> %d candidate(s) ranked, %d clear the %.2f threshold",
        len(ranked), len(above), threshold,
    )
    set_matched_suppliers(thread_row_id, above, conn)
    set_pipeline_stage(thread_row_id, "matched", conn)
    set_drafts_total(thread_row_id, len(above), conn)
    for c in above:
        logger.info("         matched: %s <%s>  score=%.3f",
                    c.get("supplier_name"), c.get("supplier_email"), c.get("match_score") or 0.0)
    progress.emit("stage", email_thread_id=thread_row_id, status="processing",
                  pipeline_stage="matched", drafts=0, drafts_total=len(above),
                  matched_suppliers=[c.get("supplier_name") for c in above])
    _demo_pause(settings, "STEP 3 (row now 'processing / matched')")
    if not above:
        finalize_thread_status(thread_row_id, "drafted", conn)
        progress.emit("done", email_thread_id=thread_row_id, status="drafted",
                      drafts=0, drafts_total=0)
        logger.warning(
            "STEP 4/5 draft: no supplier >= %.2f (top=%.3f) -> nothing to draft",
            threshold, ranked[0]["match_score"] if ranked else 0.0,
        )
        logger.info("================ PIPELINE END: DRAFTED 0 outreach, thread %s ================",
                    thread_row_id)
        return {
            "status": "drafted",
            "email_thread_id": thread_row_id,
            "thread_id": thread_row_id,
            "supplier_quote_request_ids": [],
            "matched_suppliers": [_matched_view(c) for c in ranked[:5]],
            "scenario": extraction.scenario,
        }

    logger.info("STEP 4/5 draft ONE shared outreach email + fan out to %d supplier(s)", len(above))
    top = above[0]
    context = {
        "product_name": top.get("product_name") or extraction.product_description,
        "product_specifications": top.get("product_specifications"),
        "unit_of_measure": top.get("unit_of_measure"),
    }
    shared = provider.draft_shared_outreach(extraction, context, settings)
    set_enquiry_draft(thread_row_id, shared, conn)
    for f in shared.get("fields", []):
        logger.info("         field  %-24s [%s] = %r", f.get("key"), f.get("tag"), f.get("value"))
    logger.info("         shared subject: %r", shared.get("subject"))
    for line in (shared.get("body") or "").splitlines():
        logger.info("         shared body | %s", line)
    set_pipeline_stage(thread_row_id, "drafting", conn)
    progress.emit("stage", email_thread_id=thread_row_id, status="processing",
                  pipeline_stage="drafting", drafts=0, drafts_total=len(above))

    draft_ids: list[int] = []
    for n, candidate in enumerate(above, 1):
        supplier_name = candidate.get("supplier_name") or "team"
        logger.info("         --- fan-out [%d/%d] : %s <%s> ---",
                    n, len(above), supplier_name, candidate.get("supplier_email"))
        progress.emit("draft_start", email_thread_id=thread_row_id, done=n - 1,
                      total=len(above), supplier=supplier_name)
        per_supplier = {
            "to": candidate.get("supplier_email") or "",
            "subject": shared["subject"],
            "body": render_for_supplier(shared["body_template"], shared["fields"], supplier_name),
            "body_template": substitute(shared["body_template"], {"supplier_name": supplier_name}),
            "fields": shared["fields"],
        }
        sqr_id = create_supplier_quote_request(
            email_thread_id=thread_row_id,
            client_id=client_id,
            candidate=candidate,
            draft=per_supplier,
            extraction=extraction,
            match_score=candidate["match_score"],
            conn=conn,
        )
        draft_ids.append(sqr_id)
        conn.commit()  # commit each send-record as it lands so the Desk count advances live
        progress.emit("draft", email_thread_id=thread_row_id, done=n, total=len(above),
                      supplier=supplier_name, sqr_id=sqr_id)
        logger.info("         [%d/%d] -> supplier_quote_requests id=%s written & committed",
                    n, len(above), sqr_id)
        _demo_pause(settings, f"fan-out {n}/{len(above)} written")

    logger.info("STEP 5/5 persist & finalize  (email_threads.status -> 'drafted', commit)")
    finalize_thread_status(thread_row_id, "drafted", conn)
    conn.commit()
    progress.emit("done", email_thread_id=thread_row_id, status="drafted",
                  drafts=len(draft_ids), drafts_total=len(above))
    result = {
        "status": "drafted",
        "email_thread_id": thread_row_id,
        "thread_id": thread_row_id,
        "supplier_quote_request_ids": draft_ids,
        "matched_suppliers": [_matched_view(c) for c in above],
        "scenario": extraction.scenario,
        "client_id": client_id,
    }
    logger.info("         result = %s", result)
    logger.info(
        "================ PIPELINE END: DRAFTED shared outreach -> %d supplier(s), thread %s ================",
        len(draft_ids), thread_row_id,
    )
    return result


__all__ = ["bare_email", "process_email"]
