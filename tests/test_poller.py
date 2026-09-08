import base64
import json

import pytest
from googleapiclient.errors import HttpError

from gmail_trigger_poc import poller
from gmail_trigger_poc.config import get_settings


class _Exec:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class _Messages:
    def __init__(self, svc):
        self._svc = svc

    def get(self, userId, id, format):  # noqa: A002 - mirrors the Gmail API kwarg
        return _Exec(self._svc.messages_by_id[id])

    def modify(self, userId, id, body):  # noqa: A002
        self._svc.modified.append(id)
        return _Exec({"labelIds": ["INBOX"]})


class _History:
    def __init__(self, svc):
        self._svc = svc

    def list(self, userId, startHistoryId, historyTypes, pageToken=None):
        if self._svc.history_error is not None:
            return _Exec(raises=self._svc.history_error)
        return _Exec(self._svc.history_response)


class _Users:
    def __init__(self, svc):
        self._svc = svc

    def messages(self):
        return _Messages(self._svc)

    def history(self):
        return _History(self._svc)

    def getProfile(self, userId):
        return _Exec({"historyId": self._svc.profile_history_id, "emailAddress": "t@e.com"})


class FakeService:
    def __init__(self, *, history_response=None, history_error=None,
                 messages_by_id=None, profile_history_id="999"):
        self.history_response = history_response or {"history": [], "historyId": "100"}
        self.history_error = history_error
        self.messages_by_id = messages_by_id or {}
        self.profile_history_id = profile_history_id
        self.modified = []

    def users(self):
        return _Users(self)


def _msg(text="hello"):
    return {
        "threadId": "t1",
        "labelIds": ["UNREAD", "INBOX"],
        "snippet": text,
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "a@b.com"},
                {"name": "Subject", "value": "hi"},
            ],
            "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
        },
    }


def _one_new_message_response(msg_id="m1", history_id="200"):
    return {
        "history": [{"messagesAdded": [{"message": {"id": msg_id, "threadId": "t1"}}]}],
        "historyId": history_id,
    }


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_HISTORY_FILE", str(tmp_path / "hist.json"))
    return get_settings()


def _saved_checkpoint(settings):
    with open(settings.history_file, encoding="utf-8") as f:
        return json.load(f)["historyId"]


def test_poll_once_advances_checkpoint_and_marks_read_on_success(settings):
    svc = FakeService(
        history_response=_one_new_message_response("m1", "200"),
        messages_by_id={"m1": _msg()},
    )
    out = poller.poll_once(svc, "100", settings)
    assert out == "200"
    assert svc.modified == ["m1"]
    assert _saved_checkpoint(settings) == "200"


def test_poll_once_holds_checkpoint_and_skips_mark_read_on_failure(settings, monkeypatch):
    def boom(_email):
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(poller, "process_email", boom)
    svc = FakeService(
        history_response=_one_new_message_response("m1", "200"),
        messages_by_id={"m1": _msg()},
    )
    out = poller.poll_once(svc, "100", settings)
    assert out == "100"  # checkpoint held so the message is re-scanned next cycle
    assert svc.modified == []  # never marked read
    assert _saved_checkpoint(settings) == "100"


def _http_error(status):
    resp = type("R", (), {"status": status, "reason": "test"})()
    return HttpError(resp, b"err")


def test_poll_once_rebaselines_and_persists_on_404(settings):
    svc = FakeService(history_error=_http_error(404), profile_history_id="999")
    out = poller.poll_once(svc, "1", settings)
    assert out == "999"
    assert _saved_checkpoint(settings) == "999"


def test_poll_once_keeps_checkpoint_on_non_404_http_error(settings):
    svc = FakeService(history_error=_http_error(500))
    out = poller.poll_once(svc, "42", settings)
    assert out == "42"
