import logging

from gmail_trigger_poc.logging_setup import (
    PACKAGE_LOGGER,
    _TaggedFormatter,
    body_block,
    configure_logging,
    preview,
)
from gmail_trigger_poc.pipeline import process_email


def test_configure_logging_is_idempotent():
    configure_logging("INFO", force=True)
    pkg = logging.getLogger(PACKAGE_LOGGER)
    n = len([h for h in pkg.handlers if getattr(h, "_gtp_handler", False)])
    configure_logging("DEBUG")
    configure_logging("DEBUG")
    n2 = len([h for h in pkg.handlers if getattr(h, "_gtp_handler", False)])
    assert n == 1 and n2 == 1
    assert pkg.level == logging.DEBUG
    assert pkg.propagate is False


def test_tag_and_level_in_plain_format():
    rec = logging.LogRecord(
        name="gmail_trigger_poc.poller", level=logging.INFO, pathname=__file__,
        lineno=1, msg="new email id=%s", args=("abc",), exc_info=None,
    )
    out = _TaggedFormatter(colour=False).format(rec)
    assert "[POLL ]" in out
    assert "INFO" in out
    assert "new email id=abc" in out


def test_colour_format_has_ansi_and_resets():
    rec = logging.LogRecord(
        name="gmail_trigger_poc.gmail_client", level=logging.WARNING, pathname=__file__,
        lineno=1, msg="boom", args=(), exc_info=None,
    )
    out = _TaggedFormatter(colour=True).format(rec)
    assert "\033[" in out and out.endswith("\033[0m")
    assert "[GMAIL]" in out


def test_preview_empty_and_short_and_truncated():
    assert preview("") == '"" (empty)'
    assert preview(None) == '"" (empty)'
    assert preview("hello world", 50) == '"hello world" (11 chars)'
    # display collapses whitespace, but the char count is of the raw text
    assert preview("a\n\n b\tc", 50) == '"a b c" (7 chars)'
    out = preview("x" * 300, 100)
    assert out.startswith('"' + "x" * 100 + '"')
    assert "(+200 more, 300 total)" in out


def test_body_block_indents_and_caps_lines():
    block = body_block("l1\nl2\nl3", prefix="  | ", max_lines=2)
    assert block == "\n  | l1\n  | l2\n  | ... (1 more line(s))"
    assert body_block("") == "\n    | <empty body>"


def test_pipeline_emits_info_log(fresh_store, caplog):
    pkg = logging.getLogger(PACKAGE_LOGGER)
    old = pkg.propagate
    pkg.propagate = True  # let pytest's root caplog handler see our records
    try:
        with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER):
            process_email({"id": "abc", "subject": "hi", "body": "hello"}, fresh_store)
    finally:
        pkg.propagate = old
    msgs = [r.getMessage() for r in caplog.records]
    assert any("process_email: id=abc" in m for m in msgs)
