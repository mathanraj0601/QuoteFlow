import base64

from gmail_trigger_poc.gmail_client import _extract_plain_text


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def test_extract_simple_body():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("hello world")}}
    assert _extract_plain_text(payload) == "hello world"


def test_extract_multipart_prefers_plain_text():
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>nope</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("yes this one")}},
        ],
    }
    assert _extract_plain_text(payload) == "yes this one"


def test_extract_nested_multipart():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("deep text")}},
                ],
            }
        ],
    }
    assert _extract_plain_text(payload) == "deep text"


def test_extract_returns_empty_when_no_text():
    assert _extract_plain_text({"mimeType": "image/png", "body": {}}) == ""
