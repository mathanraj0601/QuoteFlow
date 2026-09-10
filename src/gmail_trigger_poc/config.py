"""Configuration, loaded from environment / .env file.

All values have sensible defaults so the app can be imported and unit-tested
without any .env present. Only the OAuth flow actually needs real files.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file:
# src/gmail_trigger_poc/config.py -> repo root) so it works no matter which
# directory the process was launched from. Fall back to a CWD-upwards search.
# Real environment variables always win over .env values.
#
# Skip it entirely under pytest: tests must be hermetic from a developer's local
# .env (e.g. a demo MATCH_SCORE_THRESHOLD would break threshold-sensitive tests).
_UNDER_PYTEST = "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ
if not _UNDER_PYTEST:
    _ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(_ROOT_ENV if _ROOT_ENV.is_file() else None)


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


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


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

    # --- Step 2: QuoteFlow pipeline (classify -> extract -> match -> draft) ----
    # SQLite relational store (source of truth for IDs).
    db_file: str = field(default_factory=lambda: os.getenv("QUOTEFLOW_DB_FILE", "quoteflow.db"))
    # Seed dataset for the one-time backfill load (`python -m gmail_trigger_poc.seed`).
    seed_file: str = field(
        default_factory=lambda: os.getenv("QUOTEFLOW_SEED_FILE", "data/quoteflow_seed.json")
    )
    # Optional demo overlay: a handful of enquiries frozen at different lifecycle
    # stages (new / processing / drafted), loaded by `seed --demo` for walkthroughs.
    demo_file: str = field(
        default_factory=lambda: os.getenv("QUOTEFLOW_DEMO_FILE", "data/demo_enquiries.json")
    )
    # ChromaDB PersistentClient directory; collection "products" holds product vectors.
    chroma_path: str = field(default_factory=lambda: os.getenv("CHROMA_PATH", "chroma_data"))
    # Embedding function: auto (Chroma's ONNX MiniLM, fall back to hash on failure)
    # | chroma_default (force MiniLM) | hash (deterministic, offline, no model).
    embedding_provider: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "auto").strip().lower()
    )
    # LLM provider: auto (Claude if a key/profile resolves, else stub)
    # | anthropic | ollama (local, via OLLAMA_HOST) | stub.
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "auto").strip().lower()
    )
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-opus-5"))
    # Roomy: Opus 5 adaptive thinking tokens count against this alongside the JSON.
    llm_max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 8000))
    # Local Ollama server (LLM_PROVIDER=ollama). Model must be pulled first:
    #   ollama pull llama3.1:8b
    ollama_host: str = field(
        default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    )
    # Seconds to wait on a single Ollama generation (CPU-only local models are slow;
    # a cold model load plus a long draft prompt can take a few minutes on a 1-3B).
    ollama_timeout: float = field(default_factory=lambda: _float("OLLAMA_TIMEOUT", 300.0))
    # Suppliers scoring at/above this against the enquiry each get a draft (v2SQ).
    # Plans state 0.65; we default to 0.50 because (a) MiniLM cosine similarity on
    # short enquiry text lands lower than the plans assumed, (b) the seed catalog
    # has one supplier per product, so a higher bar collapses the fan-out, and
    # (c) the plans' own open question #7 flags this cutoff as tunable. Override
    # with MATCH_SCORE_THRESHOLD.
    match_score_threshold: float = field(
        default_factory=lambda: _float("MATCH_SCORE_THRESHOLD", 0.50)
    )
    # How many product candidates to pull from the vector store before the SQL join.
    match_top_n: int = field(default_factory=lambda: _int("MATCH_TOP_N", 10))
    # Artificial pause (seconds) after each pipeline stage so the Desk's live
    # "Processing" badge / trace is watchable in a demo. 0 = no delay (default).
    pipeline_step_delay_seconds: float = field(
        default_factory=lambda: _float("PIPELINE_STEP_DELAY_SECONDS", 0.0)
    )
    # Log the full prompt sent to the LLM and its raw response ([LLM] tag, INFO).
    llm_log_io: bool = field(default_factory=lambda: _bool("LLM_LOG_IO", True))
    # Broker identity used in drafted supplier outreach (matches the seed generator).
    broker_name: str = field(
        default_factory=lambda: os.getenv("BROKER_NAME", "Meridian Trade Partners")
    )
    broker_email: str = field(
        default_factory=lambda: os.getenv("BROKER_EMAIL", "procurement@meridiantrade.com")
    )


def get_settings() -> Settings:
    """Fresh Settings from the current environment (re-reads os.environ)."""
    return Settings()
