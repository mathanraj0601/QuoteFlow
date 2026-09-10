"""Architect-review regression tests: idempotency edges, client reuse, draft edits."""

import pytest

from gmail_trigger_poc.pipeline import process_email
from gmail_trigger_poc.repository import derive_thread_id

ENQUIRY = {
    "id": "rev-bolts-1",
    "thread_id": "1a2b3c",
    "from": '"John Meyer" <john@apex-mfg.com>',   # already client id 1 in the seed
    "to": "procurement@meridiantrade.com",
    "subject": "Enquiry: Stainless Steel Bolts M10x50 - 5,000 pieces",
    "body": (
        "- Product: Stainless Steel Bolts M10x50\n- Quantity required: 5,000 pieces\n"
        "- Preferred delivery terms: FOB Shanghai\n- Specifications: Grade A2-70, DIN 933\n"
        "- Delivery location: Ohio, USA\n"
    ),
}


def test_seed_client_address_is_reused_not_duplicated(fresh_store, fresh_conn):
    before = fresh_conn.execute("SELECT COUNT(*) AS c FROM clients").fetchone()["c"]
    result = process_email(ENQUIRY, fresh_store)
    after = fresh_conn.execute("SELECT COUNT(*) AS c FROM clients").fetchone()["c"]
    assert before == after == 15  # no new client row
    assert result["client_id"] == 1  # John Meyer / Apex Manufacturing


def test_zero_draft_enquiry_replays_as_drafted_not_logged(fresh_store, monkeypatch):
    monkeypatch.setenv("MATCH_SCORE_THRESHOLD", "0.99")  # nothing will clear this
    email = {**ENQUIRY, "id": "rev-zero-1"}

    first = process_email(email)            # picks up the 0.99 threshold
    assert first["status"] == "drafted"
    assert first["supplier_quote_request_ids"] == []

    second = process_email(email)
    assert second["status"] == "drafted"           # consistent with the first call
    assert second["idempotent_replay"] is True
    assert second["supplier_quote_request_ids"] == []
    assert second["scenario"] == "client_enquiry"


def test_non_enquiry_replays_as_logged(fresh_store):
    email = {
        "id": "rev-ooo-1", "thread_id": "z", "from": "bot@corp.com",
        "to": "procurement@meridiantrade.com",
        "subject": "Automatic reply: Out of office", "body": "Away until Monday.",
    }
    assert process_email(email, fresh_store)["status"] == "logged"
    replay = process_email(email, fresh_store)
    assert replay["status"] == "logged" and replay["idempotent_replay"] is True


def test_derive_thread_id_is_stable_non_negative_and_off_seed_range():
    a = derive_thread_id("1936f2abc", "msg-1")
    assert a == derive_thread_id("1936f2abc", "msg-2")  # keyed on thread, not message
    assert a >= 0 and a > 26                            # never collides with seed 1..26
    assert derive_thread_id(None, "not-hex-!!") >= 0    # CRC32 fallback
    assert derive_thread_id(None, None) == 0


class TestDraftEdits:
    @pytest.fixture
    def drafted(self, fresh_store):
        from fastapi.testclient import TestClient

        from gmail_trigger_poc.app import app

        with TestClient(app) as c:
            body = c.post("/process-email", json={**ENQUIRY, "id": "rev-edit-1"}).json()
            yield c, body

    def test_patch_fields_rerenders_canonical_body(self, drafted):
        c, body = drafted
        tid = body["email_thread_id"]
        sqr = c.get(f"/enquiry/{tid}/supplier-drafts").json()["supplier_drafts"][0]
        fields = sqr["draft_fields"]
        for f in fields:
            if f["key"] == "quantity":
                f["value"] = "9,999 widgets"

        r = c.patch(f"/supplier-quote-requests/{sqr['id']}", json={"fields": fields})
        assert r.status_code == 200
        assert "9,999 widgets" in r.json()["draft_body"]         # re-rendered from template
        assert "{{quantity}}" not in r.json()["draft_body"]

    def test_patch_rejects_bad_edit_mode(self, drafted):
        c, body = drafted
        tid = body["email_thread_id"]
        sqr_id = c.get(f"/enquiry/{tid}/supplier-drafts").json()["supplier_drafts"][0]["id"]
        assert c.patch(f"/supplier-quote-requests/{sqr_id}",
                       json={"draft_edit_mode": "nonsense"}).status_code == 400

    def test_send_persists_body_edit_and_marks_sent(self, drafted):
        c, body = drafted
        tid = body["email_thread_id"]
        sqr_id = c.get(f"/enquiry/{tid}/supplier-drafts").json()["supplier_drafts"][0]["id"]
        r = c.post(f"/supplier-quote-requests/{sqr_id}/send",
                   json={"body": "Operator rewrote this entirely."})
        assert r.status_code == 200
        d = r.json()["supplier_draft"]
        assert d["status"] == "sent" and d["draft_body"] == "Operator rewrote this entirely."

    def test_unknown_ids_404(self, drafted):
        c, _ = drafted
        assert c.get("/enquiry/999999/supplier-drafts").status_code == 404
        assert c.patch("/supplier-quote-requests/999999", json={"draft_to": "x"}).status_code == 404
        assert c.post("/supplier-quote-requests/999999/send", json={}).status_code == 404


def test_enquiries_hides_seed_backfill_by_default(fresh_store):
    from fastapi.testclient import TestClient

    from gmail_trigger_poc.app import app

    with TestClient(app) as c:
        assert c.get("/enquiries").json()["count"] == 0           # seed rows hidden
        assert c.get("/enquiries?include_all=true").json()["count"] == 52  # 26 enq + 26 supplier replies
        c.post("/process-email", json={**ENQUIRY, "id": "rev-desk-1"})
        listed = c.get("/enquiries").json()
        assert listed["count"] == 1
        assert listed["enquiries"][0]["scenario"] == "client_enquiry"
