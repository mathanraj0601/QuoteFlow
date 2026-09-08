# gmail-trigger-poc

Local POC for **QuoteFlow Step 1**: a Python process polls a Gmail inbox and hands
every new email to `process_email()`. No public server, webhook, or domain — it
runs entirely on localhost using **polling** (`users.history.list`), not Pub/Sub.

Step 2 (`classify → extract → match → draft`) is **not built yet** — `process_email()`
is a stub that echoes the email back.

```
Gmail inbox (test account)
      │  poll every 20s (background daemon thread)
      ▼
Single FastAPI process  (uvicorn gmail_trigger_poc.app:app)
      ├── poll_gmail() thread ──┐
      │                         ▼
      │                  process_email()   ← Step 2 hook, one shared method
      │                         ▲
      └── POST /process-email ──┘  thin wrapper for curl/Postman
```

## Layout

| Path | Purpose |
|---|---|
| `src/gmail_trigger_poc/config.py` | Settings loaded from env / `.env` |
| `src/gmail_trigger_poc/logging_setup.py` | Coloured `[TAG]` logging, `configure_logging()` |
| `src/gmail_trigger_poc/auth.py` | OAuth — `get_gmail_service()` (JSON token, not pickle) |
| `src/gmail_trigger_poc/gmail_client.py` | history checkpoint, message fetch, mark-as-read |
| `src/gmail_trigger_poc/poller.py` | `poll_gmail()` background loop |
| `src/gmail_trigger_poc/pipeline.py` | `process_email()` — Step 2 hook (stub) |
| `src/gmail_trigger_poc/app.py` | FastAPI app + poller thread on startup |
| `docs/gmail-integration-guide.md` | Full guide + architect review |

## Logging

Every subsystem logs through the `gmail_trigger_poc` logger with a colour-coded tag:

```
18:04:17 INFO  [POLL ] poller started (interval=5s, baseline historyId=2003)
18:04:23 DEBUG [GMAIL] history.list page=1 -> 0 record(s) historyId=2003
18:04:30 INFO  [PIPE ] process_email: id=demo1 subject='Coloured log demo' body_chars=10
18:04:30 INFO  [HTTP ] <-- POST /process-email 200 (2.2 ms)
```

| Tag | Subsystem | Tag | Subsystem |
|---|---|---|---|
| `AUTH` | OAuth / token | `POLL` | polling loop |
| `GMAIL` | raw Gmail API calls | `PIPE` | `process_email` pipeline |
| `HTTP` | FastAPI request layer | `CFG` | config / startup |

Control it: `LOG_LEVEL=DEBUG` (per-cycle detail) / `INFO` (default) / `WARNING`.
`LOG_COLOR=1` forces colour, `LOG_COLOR=0` or `NO_COLOR=1` disables it (auto-detected by default).

**Email body** — every fetched message logs aligned sub-fields:

```
18:13:04 INFO  [POLL ] NEW EMAIL #1  id=1936f...  (fetching full message)
18:13:04 INFO  [GMAIL]      from   : Jane Buyer <jane@acme.example>
18:13:04 INFO  [GMAIL]      to     : testusergmail22@gmail.com
18:13:04 INFO  [GMAIL]      subject: RFQ: 50x widget-A
18:13:04 INFO  [GMAIL]      snippet: "Hi team, Please quote 50 units ..." (312 chars)
18:13:04 INFO  [GMAIL]      body   : "Hi team, Please quote 50 units of widget-A ..." (312 chars)
18:13:04 INFO  [GMAIL] marked id=1936f... as read (labels now: ['INBOX', 'CATEGORY_PERSONAL'])
```

- `body` preview is whitespace-collapsed to one line; the char count is of the raw text.
- `LOG_BODY_PREVIEW_CHARS=200` — preview length.
- `LOG_FULL_BODY=true` — also dump the entire decoded body as an indented `| ` block (DEBUG only).
- `LOG_HEARTBEAT_EVERY=12` — `still polling - N cycles, M idle` line every N idle cycles (0 = off).

## Quick start

```bash
uv sync                      # install
cp .env.example .env         # then edit
# put credentials.json (Google Cloud OAuth client, Desktop app) in this folder
uv run gmail-trigger-poc     # or: uv run uvicorn gmail_trigger_poc.app:app --reload
```

First run opens a browser once for OAuth consent, then caches `token.json`.

```bash
curl localhost:8000/health
curl -X POST localhost:8000/process-email -H 'content-type: application/json' \
  -d '{"id":"m1","thread_id":"t1","from":"a@b.com","subject":"Quote","body":"50 widgets"}'
```

## Tests

```bash
uv run pytest
```

## Environment variables

See `.env.example` — every value has a default, so only `credentials.json` must
actually exist. Full table in `docs/gmail-integration-guide.md` §2.
