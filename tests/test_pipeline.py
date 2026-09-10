"""End-to-end pipeline tests (stub LLM + hash embeddings, seeded throwaway store)."""

from gmail_trigger_poc.pipeline import process_email

BOLTS_ENQUIRY = {
    "id": "pytest-bolts-1",
    "thread_id": "1a2b3c",
    "from": '"John Meyer" <john@apex-mfg.com>',
    "to": "procurement@meridiantrade.com",
    "subject": "Enquiry: Stainless Steel Bolts M10x50 - 5,000 pieces needed",
    "body": (
        "Dear Team,\n\nWe are sourcing Stainless Steel Bolts M10x50 and would like a quotation.\n"
        "- Product: Stainless Steel Bolts M10x50\n"
        "- Quantity required: 5,000 pieces\n"
        "- Preferred delivery terms: FOB Shanghai\n"
        "- Specifications: Grade A2-70, DIN 933\n"
        "- Delivery location: Ohio, USA\n\nBest regards,\nJohn"
    ),
}


def test_non_enquiry_is_logged_not_drafted(fresh_store, fresh_conn):
    email = {
        "id": "pytest-ooo-1", "thread_id": "zz", "from": "bot@corp.com",
        "to": "procurement@meridiantrade.com",
        "subject": "Automatic reply: Out of office", "body": "I am away until Monday.",
    }
    result = process_email(email, fresh_store)
    assert result["status"] == "logged"
    assert result["scenario"] != "client_enquiry"

    row = fresh_conn.execute(
        "SELECT * FROM email_threads WHERE gmail_message_id = ?", (email["id"],)
    ).fetchone()
    assert row is not None and row["direction"] == "inbound"
    assert row["status"] == "logged"  # v2SQ lifecycle column
    assert fresh_conn.execute(
        "SELECT COUNT(*) AS c FROM supplier_quote_requests WHERE email_thread_id = ?",
        (row["id"],),
    ).fetchone()["c"] == 0


def test_client_enquiry_drafts_supplier_outreach(fresh_store, fresh_conn):
    result = process_email(BOLTS_ENQUIRY, fresh_store)
    assert result["status"] == "drafted"
    ids = result["supplier_quote_request_ids"]
    assert len(ids) >= 1

    thread = fresh_conn.execute(
        "SELECT status, pipeline_stage FROM email_threads WHERE id = ?",
        (result["email_thread_id"],),
    ).fetchone()
    assert thread["status"] == "drafted" and thread["pipeline_stage"] == "drafted"

    rows = fresh_conn.execute(
        "SELECT * FROM supplier_quote_requests WHERE id IN (%s)"
        % ",".join("?" * len(ids)),
        ids,
    ).fetchall()
    assert len(rows) == len(ids)
    for r in rows:
        assert r["status"] == "draft"
        assert r["draft_to"] and r["draft_subject"] and r["draft_body"]
        assert r["draft_body_template"] and r["draft_fields_json"]
        assert r["email_thread_id"] == result["email_thread_id"]
    # every matched supplier specialises in fasteners for this enquiry
    specs = {m["supplier_specialization"] for m in result["matched_suppliers"]}
    assert specs == {"Industrial Fasteners"}


def test_matched_suppliers_and_shared_draft_snapshotted(fresh_store, fresh_conn):
    result = process_email({**BOLTS_ENQUIRY, "id": "pytest-bolts-shared"}, fresh_store)
    tid = result["email_thread_id"]
    row = fresh_conn.execute(
        "SELECT matched_suppliers_json, draft_subject, draft_body_template, draft_fields_json "
        "FROM email_threads WHERE id = ?",
        (tid,),
    ).fetchone()
    import json as _json

    matched = _json.loads(row["matched_suppliers_json"])
    assert matched and {"supplier_id", "supplier_name", "match_score"} <= set(matched[0])
    # one shared draft, {{supplier_name}} kept live, no price token/field
    assert "{{supplier_name}}" in row["draft_body_template"]
    assert "unit_price" not in row["draft_body_template"]
    fields = _json.loads(row["draft_fields_json"])
    assert not any("price" in f["key"] for f in fields)
    # every per-supplier send record has {{supplier_name}} already resolved
    for r in fresh_conn.execute(
        "SELECT draft_body FROM supplier_quote_requests WHERE email_thread_id = ?", (tid,)
    ):
        assert "{{supplier_name}}" not in r["draft_body"]


def test_process_email_is_idempotent(fresh_store, fresh_conn):
    first = process_email({**BOLTS_ENQUIRY, "id": "pytest-bolts-idem"}, fresh_store)
    before = fresh_conn.execute(
        "SELECT COUNT(*) AS c FROM supplier_quote_requests"
    ).fetchone()["c"]

    second = process_email({**BOLTS_ENQUIRY, "id": "pytest-bolts-idem"}, fresh_store)
    after = fresh_conn.execute(
        "SELECT COUNT(*) AS c FROM supplier_quote_requests"
    ).fetchone()["c"]

    assert second.get("idempotent_replay") is True
    assert second["supplier_quote_request_ids"] == first["supplier_quote_request_ids"]
    assert before == after  # no duplicate rows


def test_enquiry_off_catalogue_still_returns_drafted(fresh_store):
    email = {
        "id": "pytest-offcat-1", "thread_id": "oc", "from": "buyer@example.com",
        "to": "procurement@meridiantrade.com",
        "subject": "Enquiry: bespoke neon signage",
        "body": "We are looking for a quote on custom neon signage for a retail storefront.",
    }
    result = process_email(email, fresh_store)
    assert result["status"] == "drafted"
    assert isinstance(result["supplier_quote_request_ids"], list)
