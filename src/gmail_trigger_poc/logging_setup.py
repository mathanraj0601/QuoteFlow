"""Central logging configuration for the whole package.

One StreamHandler on the ``gmail_trigger_poc`` logger, formatted with a short
colour-coded ``[TAG]`` per subsystem so the code flow is easy to scan:

    17:57:03 INFO  [POLL ] poller started (interval=10s, baseline historyId=1683)
    17:57:13 DEBUG [GMAIL] history.list page: 0 record(s) historyId=1683
    17:57:24 INFO  [POLL ] new email id=1936f from='a@b.com' subject='Quote'
    17:57:24 INFO  [PIPE ] process_email: id=1936f subject='Quote' body_chars=42
    ^time    ^lvl   ^tag   ^message

Tags:  AUTH  OAuth / token          POLL  polling loop
       GMAIL raw Gmail API calls    PIPE  process_email pipeline
       HTTP  FastAPI request layer  CFG   config / startup

- Colour is on when stdout is a TTY and ``NO_COLOR`` is unset; force it with
  ``LOG_COLOR=1`` / disable with ``LOG_COLOR=0``.
- Thread name is appended when it is not the main thread (the poller runs on the
  ``gmail-poller`` daemon thread).
- Noisy third-party loggers are pinned to WARNING so DEBUG stays readable.
- ``propagate`` is off so this handler does not double-log via uvicorn's root
  logger. Idempotent: safe to call from ``run()`` and the FastAPI lifespan.
"""

from __future__ import annotations

import logging
import os
import re
import sys

PACKAGE_LOGGER = "gmail_trigger_poc"
_DATE_FORMAT = "%H:%M:%S"

# --- ANSI colours ----------------------------------------------------------
_RESET = "\033[0m"
# Short, fixed 5-char level names so every line aligns.
logging.addLevelName(logging.WARNING, "WARN")
logging.addLevelName(logging.CRITICAL, "CRIT")
_LEVEL_COLOUR = {
    "DEBUG": "\033[2m",       # dim
    "INFO": "\033[32m",       # green
    "WARN": "\033[33m",       # yellow
    "ERROR": "\033[31m",      # red
    "CRIT": "\033[1;41m",     # bold white on red
}
# module suffix -> (short tag, colour)
_TAG_MAP = {
    "auth": ("AUTH ", "\033[35m"),          # magenta
    "poller": ("POLL ", "\033[36m"),        # cyan
    "gmail_client": ("GMAIL", "\033[34m"),  # blue
    "pipeline": ("PIPE ", "\033[32m"),      # green
    "app": ("HTTP ", "\033[33m"),           # yellow
    "config": ("CFG  ", "\033[2m"),         # dim
    "logging_setup": ("CFG  ", "\033[2m"),
    "gmail_trigger_poc": ("CFG  ", "\033[2m"),  # the package root logger itself
}
_DEFAULT_TAG = ("MISC ", "\033[37m")

_NOISY_LOGGERS = (
    "googleapiclient.discovery_cache",
    "googleapiclient.discovery",
    "google.auth.transport.requests",
    "google_auth_httplib2",
    "google.auth._default",
    "urllib3",
    "httpx",
    "httpcore",
    "oauthlib",
    "requests_oauthlib",
)

_configured = False


def _colour_enabled() -> bool:
    override = os.getenv("LOG_COLOR")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "always", "on"}
    if os.getenv("NO_COLOR") is not None:
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


class _TaggedFormatter(logging.Formatter):
    def __init__(self, *, colour: bool) -> None:
        super().__init__(datefmt=_DATE_FORMAT)
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        suffix = record.name.split(".")[-1]
        tag, tag_colour = _TAG_MAP.get(suffix, _DEFAULT_TAG)
        ts = self.formatTime(record, self.datefmt)
        level = record.levelname
        msg = record.getMessage()
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"

        thread = "" if record.threadName == "MainThread" else f" ({record.threadName})"

        if self.colour:
            lc = _LEVEL_COLOUR.get(level, "")
            return (
                f"\033[2m{ts}{_RESET} "
                f"{lc}{level:<5}{_RESET} "
                f"{tag_colour}[{tag}]{_RESET} "
                f"{msg}\033[2m{thread}{_RESET}"
            )
        return f"{ts} {level:<5} [{tag}] {msg}{thread}"


def configure_logging(level: str | int = "INFO", *, force: bool = False) -> logging.Logger:
    """Attach the package handler and set levels. Returns the package logger."""
    global _configured

    pkg_logger = logging.getLogger(PACKAGE_LOGGER)

    if _configured and not force:
        pkg_logger.setLevel(level)
        return pkg_logger

    for h in list(pkg_logger.handlers):
        if getattr(h, "_gtp_handler", False):
            pkg_logger.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_TaggedFormatter(colour=_colour_enabled()))
    handler._gtp_handler = True  # type: ignore[attr-defined]

    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(level)
    pkg_logger.propagate = False

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    pkg_logger.debug("logging configured (level=%s)", logging.getLevelName(pkg_logger.level))
    return pkg_logger


def get_logger(name: str) -> logging.Logger:
    """`logging.getLogger` for a submodule, e.g. get_logger(__name__)."""
    return logging.getLogger(name)


# --- text helpers for logging email content ------------------------------

_WS_RUN = re.compile(r"\s+")


def preview(text: str | None, limit: int = 200) -> str:
    """One-line, whitespace-collapsed snippet; char count is of the RAW text.

    `"Hi there ..." (312 chars)`  /  `"Hi there" (+840 more, 900 total)`
    """
    if not text:
        return '"" (empty)'
    total = len(text)
    flat = _WS_RUN.sub(" ", text).strip()
    if len(flat) <= limit:
        return f'"{flat}" ({total} chars)'
    return f'"{flat[:limit]}" (+{total - limit} more, {total} total)'


def body_block(text: str | None, *, prefix: str = "    | ", max_lines: int = 40) -> str:
    """Multi-line indented block for dumping a full body at DEBUG.

    Returns a leading-newline string so it sits under its log line:

        [GMAIL] full body id=... (3 lines):
            | Hi, please quote 50 units
            | of part X.
            | Regards, John
    """
    if not text:
        return f"\n{prefix}<empty body>"
    lines = text.splitlines() or [text]
    shown = lines[:max_lines]
    out = "\n".join(prefix + ln for ln in shown)
    if len(lines) > max_lines:
        out += f"\n{prefix}... ({len(lines) - max_lines} more line(s))"
    return "\n" + out
