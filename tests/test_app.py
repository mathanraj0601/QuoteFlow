import os

os.environ["ENABLE_POLLER"] = "false"  # don't spawn the poller during tests

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gmail_trigger_poc.app import app  # noqa: E402

ENQUIRY = {
    "id": "api-bolts-1",
    "thread_id": "9f9f9f",
    "from": '"John Meyer" <john@apex-mfg.com>',
    "to": "procurement@meridiantrade.com",
    "subject": "Enquiry: Stainless Steel Bolts M10x50 - 5,000 pieces needed",
    "body": (
        "Dear Team,\nWe are sourcing Stainless Steel Bolts M10x50.\n"
        "- Product: Stainless Steel Bolts M10x50\n"
        "- Quantity required: 5,000 pieces\n"
        "- Preferred delivery terms: FOB Shanghai\n"
        "- Specifications: Grade A2-70, DIN 933\n- Delivery location: Ohio, USA\n"
    ),
}


@pytest.fixture
def client(fresh_store):
    with TestClient(app) as c:
        yield c


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "QuoteFlow" in r.text
    # React build (assets shell) or the no-build fallback (inline API calls)
    assert "/assets/" in r.text or "/enquiry/" in r.text


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["poller_enabled"] is False
    assert body["has_db"] is True


def test_process_email_endpoint_drafts_outreach(client):
    r = client.post("/process-email", json=ENQUIRY)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "drafted"
    assert len(body["supplier_quote_request_ids"]) >= 1


def test_process_email_endpoint_non_enquiry_logged(client):
    r = client.post("/process-email", json={
        "id": "api-ooo-1", "thread_id": "x", "from": "bot@corp.com",
        "to": "procurement@meridiantrade.com",
        "subject": "Automatic reply: Out of office", "body": "Away until Monday.",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "logged"


def test_supplier_drafts_roundtrip_and_send(client):
    drafted = client.post("/process-email", json=ENQUIRY).json()
    thread_id = drafted["email_thread_id"]
    ids = drafted["supplier_quote_request_ids"]

    got = client.get(f"/enquiry/{thread_id}/supplier-drafts").json()
    assert got["email_thread_id"] == thread_id
    assert got["status"] == "drafted"
    assert [n["key"] for n in got["trace"]] == [
        "received", "extracted", "matched", "generated", "all_sent",
    ]
    assert all(n["done"] for n in got["trace"][:4])  # generated once drafted
    assert got["trace"][4]["done"] is False  # nothing sent yet
    assert got["draft_progress"] == {"done": len(ids), "total": len(ids)}
    assert {d["id"] for d in got["supplier_drafts"]} == set(ids)
    scores = [d["match_score"] for d in got["supplier_drafts"]]
    assert scores == sorted(scores, reverse=True)  # best match first
    assert got["progress"] == {"sent": 0, "total": len(ids)}
    assert got["original_email"]["subject"] == ENQUIRY["subject"]
    first = got["supplier_drafts"][0]
    assert first["draft_fields"] and first["draft_body"]

    sent = client.post(f"/supplier-quote-requests/{ids[0]}/send", json={})
    assert sent.status_code == 200
    payload = sent.json()
    assert payload["status"] == "sent" and payload["transmitted"] is False
    assert payload["supplier_draft"]["status"] == "sent"
    assert payload["progress"] == {"sent": 1, "total": len(ids)}

    # second send is a conflict
    assert client.post(f"/supplier-quote-requests/{ids[0]}/send", json={}).status_code == 409


def test_shared_enquiry_draft_and_multi_send(client):
    drafted = client.post("/process-email", json=ENQUIRY).json()
    tid = drafted["email_thread_id"]

    got = client.get(f"/enquiry/{tid}/supplier-drafts").json()
    # one shared draft, {{supplier_name}} left as a live placeholder, no price
    ed = got["enquiry_draft"]
    assert ed and ed["subject"]
    assert "{{supplier_name}}" in ed["body_template"]
    keys = {f["key"] for f in ed["fields"]}
    assert not any("price" in k for k in keys)
    # matched-supplier checklist
    ms = got["matched_suppliers"]
    assert len(ms) == len(drafted["supplier_quote_request_ids"])
    assert all(m["status"] in ("pending", "draft") for m in ms)
    assert got["trace"][2]["detail"]  # names or count on the "matched" node

    # edit the shared draft once
    new_fields = [
        {**f, "value": "7,500 pieces"} if f["key"] == "quantity" else f
        for f in ed["fields"]
    ]
    r = client.patch(f"/enquiry/{tid}/draft",
                     json={"draft_subject": "RFQ - bolts (edited)", "fields": new_fields})
    assert r.status_code == 200
    assert "7,500 pieces" in r.json()["body"]
    assert r.json()["subject"] == "RFQ - bolts (edited)"

    # send to a subset in one call
    first_id = ms[0]["supplier_id"]
    sent = client.post(f"/enquiry/{tid}/send", json={"supplier_ids": [first_id]})
    assert sent.status_code == 200
    payload = sent.json()
    assert payload["status"] == "sent" and payload["transmitted"] is False
    assert payload["newly_sent"] == [first_id]
    assert payload["progress"] == {"sent": 1, "total": len(ms)}

    again = client.get(f"/enquiry/{tid}/supplier-drafts").json()
    sent_row = next(m for m in again["matched_suppliers"] if m["supplier_id"] == first_id)
    assert sent_row["status"] == "sent" and sent_row["sent_at"]
    # the sent snapshot carries the edited quantity and this supplier's name
    d = next(x for x in again["supplier_drafts"] if x["supplier_id"] == first_id)
    assert "7,500 pieces" in d["draft_body"]
    assert "{{supplier_name}}" not in d["draft_body"]

    # empty / bad selection is a 400
    assert client.post(f"/enquiry/{tid}/send", json={"supplier_ids": []}).status_code == 400


def test_send_endpoint_unknown_enquiry_404(client):
    assert client.post("/enquiry/999999/send", json={"supplier_ids": [1]}).status_code == 404
    assert client.patch("/enquiry/999999/draft", json={"draft_subject": "x"}).status_code == 404


def test_send_enquiry_conflict_when_nothing_new_to_send(client):
    drafted = client.post("/process-email", json=ENQUIRY).json()
    tid = drafted["email_thread_id"]
    ms = client.get(f"/enquiry/{tid}/supplier-drafts").json()["matched_suppliers"]
    first = ms[0]["supplier_id"]

    assert client.post(f"/enquiry/{tid}/send", json={"supplier_ids": [first]}).status_code == 200
    # same supplier again -> already sent -> 409, not a silent no-op
    assert client.post(f"/enquiry/{tid}/send", json={"supplier_ids": [first]}).status_code == 409
    # a supplier id that isn't part of this enquiry -> 409
    assert client.post(f"/enquiry/{tid}/send", json={"supplier_ids": [987654]}).status_code == 409
    # malformed payloads -> 400
    assert client.post(f"/enquiry/{tid}/send", json={"supplier_ids": []}).status_code == 400
    assert client.post(f"/enquiry/{tid}/send", json={"supplier_ids": "x"}).status_code == 400
    assert client.post(f"/enquiry/{tid}/send", json={}).status_code == 400


def test_logged_thread_detail_and_desk_shape(client):
    r = client.post("/process-email", json={
        "id": "api-ooo-2", "thread_id": "y", "from": "bot@corp.com",
        "to": "procurement@meridiantrade.com",
        "subject": "Automatic reply: Out of office", "body": "Back Monday.",
    }).json()
    tid = r["email_thread_id"]
    assert r["status"] == "logged"

    got = client.get(f"/enquiry/{tid}/supplier-drafts").json()
    assert got["status"] == "logged"
    assert got["enquiry_draft"] is None
    assert got["matched_suppliers"] == []
    assert got["supplier_drafts"] == []
    assert got["pipeline_stage"] == "extracted"
    # received + extracted done, nothing past that
    keys_done = {n["key"]: n["done"] for n in got["trace"]}
    assert keys_done["received"] is True and keys_done["extracted"] is True
    assert keys_done["matched"] is False and keys_done["all_sent"] is False

    desk = client.get("/enquiries?include_all=true").json()["enquiries"]
    row = next(e for e in desk if e["email_thread_id"] == tid)
    assert row["status"] == "logged" and row["drafts"] == 0 and row["all_sent"] is False


def test_enquiries_row_status_stage_in_sync_after_drafting(client):
    drafted = client.post("/process-email", json=ENQUIRY).json()
    tid = drafted["email_thread_id"]
    row = next(e for e in client.get("/enquiries").json()["enquiries"]
              if e["email_thread_id"] == tid)
    assert row["status"] == "drafted"
    assert row["pipeline_stage"] == "drafted"
    assert row["drafts"] == len(drafted["supplier_quote_request_ids"])
    assert row["drafts_total"] == row["drafts"]
    assert row["all_sent"] is False
    assert row["sent"] == 0


def test_patch_enquiry_draft_free_text_and_validation(client):
    tid = client.post("/process-email", json=ENQUIRY).json()["email_thread_id"]

    # free-text mode: the body is stored and returned verbatim (tokens kept)
    r = client.patch(f"/enquiry/{tid}/draft", json={
        "draft_edit_mode": "free_text",
        "draft_body": "Hi {{supplier_name}} — bespoke note from the operator.",
    })
    assert r.status_code == 200
    assert r.json()["edit_mode"] == "free_text"
    assert r.json()["body"] == "Hi {{supplier_name}} — bespoke note from the operator."

    # bad edit mode -> 400 ; empty patch -> 400
    assert client.patch(f"/enquiry/{tid}/draft", json={"draft_edit_mode": "nope"}).status_code == 400
    assert client.patch(f"/enquiry/{tid}/draft", json={}).status_code == 400


def test_patch_persists_edits(client):
    drafted = client.post("/process-email", json=ENQUIRY).json()
    sqr_id = drafted["supplier_quote_request_ids"][0]

    r = client.patch(f"/supplier-quote-requests/{sqr_id}",
                     json={"draft_subject": "RFQ - edited by operator"})
    assert r.status_code == 200 and r.json()["draft_subject"] == "RFQ - edited by operator"

    again = client.get(
        f"/enquiry/{drafted['email_thread_id']}/supplier-drafts"
    ).json()
    edited = next(d for d in again["supplier_drafts"] if d["id"] == sqr_id)
    assert edited["draft_subject"] == "RFQ - edited by operator"


def test_suppliers_directory(client):
    body = client.get("/suppliers").json()
    assert body["count"] == 18
    assert all({"id", "name", "specialization"} <= set(s) for s in body["suppliers"])


def test_enquiries_desk_list(client):
    client.post("/process-email", json=ENQUIRY)
    body = client.get("/enquiries").json()
    assert body["count"] >= 1
    mine = next(e for e in body["enquiries"] if e["subject"] == ENQUIRY["subject"])
    assert mine["status"] == "drafted" and mine["drafts"] >= 1
    assert mine["pipeline_stage"] == "drafted"
    assert mine["all_sent"] is False
