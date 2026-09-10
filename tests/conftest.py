"""Shared test setup: an isolated, hash-embedded, stub-LLM QuoteFlow store.

Env is pinned before any `gmail_trigger_poc` import so `get_settings()` picks it
up: deterministic hash embeddings (no model download), the stub LLM provider, and
a throwaway SQLite file + Chroma dir under a temp directory.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="qf-test-"))

os.environ.setdefault("ENABLE_POLLER", "false")
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["LLM_PROVIDER"] = "stub"
os.environ["QUOTEFLOW_DB_FILE"] = str(_TMP / "quoteflow.db")
os.environ["CHROMA_PATH"] = str(_TMP / "chroma")
os.environ.setdefault("QUOTEFLOW_SEED_FILE", "data/quoteflow_seed.json")

import pytest  # noqa: E402

from gmail_trigger_poc import seed as _seed  # noqa: E402
from gmail_trigger_poc.config import get_settings  # noqa: E402
from gmail_trigger_poc.db import connect  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _seeded_store():
    """Seed the shared read-only store once for the whole test session."""
    _seed.run(get_settings(), reset=True)


@pytest.fixture
def seeded_settings(_seeded_store):
    """Settings pointing at the shared, seeded, read-only store."""
    return get_settings()


@pytest.fixture
def conn(seeded_settings):
    c = connect(seeded_settings)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """A private seeded store for tests that write (pipeline / API)."""
    monkeypatch.setenv("QUOTEFLOW_DB_FILE", str(tmp_path / "quoteflow.db"))
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    settings = get_settings()
    _seed.run(settings, reset=True)
    return settings


@pytest.fixture
def fresh_conn(fresh_store):
    c = connect(fresh_store)
    try:
        yield c
    finally:
        c.close()
