import importlib

from gmail_trigger_poc import config


def test_defaults(monkeypatch):
    for var in [
        "GMAIL_CREDENTIALS_FILE", "GMAIL_TOKEN_FILE", "GMAIL_HISTORY_FILE",
        "GMAIL_USER_ID", "GMAIL_POLL_INTERVAL_SECONDS", "GMAIL_SCOPES",
        "ENABLE_POLLER", "OAUTH_LOCAL_SERVER_PORT",
    ]:
        monkeypatch.delenv(var, raising=False)

    s = config.get_settings()
    assert s.credentials_file == "credentials.json"
    assert s.token_file == "token.json"
    assert s.user_id == "me"
    assert s.poll_interval_seconds == 20
    assert s.scopes == ["https://www.googleapis.com/auth/gmail.modify"]
    assert s.enable_poller is True
    assert s.oauth_local_server_port == 0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("GMAIL_POLL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("ENABLE_POLLER", "false")
    monkeypatch.setenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    )
    s = config.get_settings()
    assert s.poll_interval_seconds == 5
    assert s.enable_poller is False
    assert s.scopes == [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    ]
