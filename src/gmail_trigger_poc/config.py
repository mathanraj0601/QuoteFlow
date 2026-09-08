"""Configuration, loaded from environment / .env file.

All values have sensible defaults so the app can be imported and unit-tested
without any .env present. Only the OAuth flow actually needs real files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load .env from the current working directory if present. Real environment
# variables always win over .env values.
load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    # comma- or whitespace-separated
    return [s for s in raw.replace(",", " ").split() if s]


DEFAULT_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass(frozen=True)
class Settings:
    # OAuth client secret downloaded from Google Cloud Console (Desktop app).
    credentials_file: str = field(
        default_factory=lambda: os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
    )
    # Where the user's access + refresh token is cached after first consent.
    token_file: str = field(
        default_factory=lambda: os.getenv("GMAIL_TOKEN_FILE", "token.json")
    )
    # Local checkpoint of the last Gmail historyId we processed.
    history_file: str = field(
        default_factory=lambda: os.getenv("GMAIL_HISTORY_FILE", "last_history_id.json")
    )
    # Gmail user to watch. "me" == the authenticated account.
    user_id: str = field(default_factory=lambda: os.getenv("GMAIL_USER_ID", "me"))
    # Seconds between polls.
    poll_interval_seconds: int = field(
        default_factory=lambda: _int("GMAIL_POLL_INTERVAL_SECONDS", 20)
    )
    # OAuth scopes requested. Change this list -> delete token.json -> re-consent.
    scopes: list[str] = field(default_factory=lambda: _list("GMAIL_SCOPES", DEFAULT_SCOPES))
    # Port for the one-time local OAuth callback server. 0 == pick a free port.
    oauth_local_server_port: int = field(
        default_factory=lambda: _int("OAUTH_LOCAL_SERVER_PORT", 0)
    )
    # Start the background poller thread on FastAPI startup.
    enable_poller: bool = field(default_factory=lambda: _bool("ENABLE_POLLER", True))
    # uvicorn bind host/port for `gmail-trigger-poc` / `python -m ...`.
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    # Logging verbosity for the `gmail_trigger_poc` logger tree.
    # DEBUG shows every poll cycle, API call, and message field.
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )
    # How many characters of an email body to show in the one-line preview.
    log_body_preview_chars: int = field(
        default_factory=lambda: _int("LOG_BODY_PREVIEW_CHARS", 200)
    )
    # Also dump the FULL decoded body as an indented block (DEBUG level).
    log_full_body: bool = field(default_factory=lambda: _bool("LOG_FULL_BODY", False))
    # Log a heartbeat line at INFO every N idle poll cycles (0 = never).
    log_heartbeat_every: int = field(
        default_factory=lambda: _int("LOG_HEARTBEAT_EVERY", 12)
    )


def get_settings() -> Settings:
    """Fresh Settings from the current environment (re-reads os.environ)."""
    return Settings()
