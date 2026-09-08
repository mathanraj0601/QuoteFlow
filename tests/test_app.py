import os

os.environ["ENABLE_POLLER"] = "false"  # don't spawn the poller during tests

from fastapi.testclient import TestClient  # noqa: E402

from gmail_trigger_poc.app import app  # noqa: E402


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["poller_enabled"] is False


def test_process_email_endpoint():
    with TestClient(app) as client:
        r = client.post(
            "/process-email",
            json={"id": "abc", "thread_id": "abc", "from": "a@b.com",
                  "subject": "hi", "body": "hello"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "received"
        assert r.json()["message_id"] == "abc"
