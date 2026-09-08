# QuoteFlow — Gmail Integration Guide

**Status:** Step 1 complete (Gmail polling + auth), architect-reviewed and aligned with the project's POC plan. Step 2 (`/process-email` pipeline) not started yet.
**Fact-checked against:** Google's official Gmail API Python quickstart and sync guide (developers.google.com/workspace/gmail/api).
**Cross-checked against:** `quoteflow-flow-document.md`, `QuoteFlow_Minimal_Version_1.md`, `quoteflow_plan_B_without_extraction.md`, `quoteflow_schema_without_extraction.sql`. See §9 for the review findings.

**Implementation note:** this repo splits the guide's single `app.py` into a small package under `src/gmail_trigger_poc/` (`config`, `auth`, `gmail_client`, `poller`, `pipeline`, `app`). The behaviour is exactly what's described below; file names in the code snippets point at the module that holds each piece.

---

## Learning checkpoint

✅ **Step 1 — understood.** Gmail OAuth setup, local credential storage, and history-based polling.

⏭️ **Step 2 — next up, not started.** The `process_email()` pipeline: classify → extract → match → draft quotation. Come back to this document, then pick up the plan under "What's next" at the bottom.

---

## 1. What this integration does

A local Python script watches a Gmail inbox and, whenever a new email arrives, pulls it in for processing — without needing a public server, webhook, or domain. It runs entirely on localhost using **polling** rather than push notifications (Pub/Sub), which is the right tradeoff for a local POC.

```
Gmail inbox (test account)
      │  polling every 20s (background thread)
      ▼
Single FastAPI process (uvicorn gmail_trigger_poc.app:app)
      │
      ├── poll_gmail() thread ──┐
      │                         ▼
      │                  process_email()  ← Step 2, not built yet — one shared method
      │                         ▲
      └── POST /process-email ──┘  (thin wrapper, for curl/Postman + external triggers)
```

**Architecture note:** `process_email()` is one method (`gmail_trigger_poc/pipeline.py`). The poller calls it directly, in-process — no HTTP hop to itself. The `/process-email` route is kept as a thin wrapper around the same method, purely so it can still be tested standalone with curl/Postman (per Plan B §7) or triggered by something other than the local poller later. See §9.1.

---

## 2. One-time setup (Google Cloud Console)

Do these in order. It takes ~10 minutes.

### 2.1 Create / pick a project
1. Go to **https://console.cloud.google.com/**
2. Top bar → project dropdown → **New Project** (or select an existing one).
   Name it e.g. `quoteflow-poc`. Note the project.

### 2.2 Enable the Gmail API
3. **APIs & Services → Library** → search **"Gmail API"** → open it → **Enable**.

### 2.3 Configure the OAuth consent screen
4. **APIs & Services → OAuth consent screen**
   - **User type: External** → Create. (Internal is only for Google Workspace orgs.)
   - App name: `QuoteFlow POC`; user support email: your address; developer contact: your address. Save and continue.
   - **Scopes** page: you can leave it empty here (the script requests scopes at runtime) → Save and continue.
   - **Test users** page → **Add users** → add the **test Gmail account** you'll be polling → Save.
   - Back to the dashboard. Leave the app in **Testing** / **Publishing status: Testing**. Do **not** click "Publish app".
   - Note: an app in Testing is capped at **100 test users** and unverified-app tokens expire after 7 days of inactivity — both irrelevant at POC scale, but that 7-day expiry is why you may occasionally have to re-consent.

### 2.4 Create the OAuth client (the file you download)
5. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - **Application type: Desktop app**
   - Name: `quoteflow-poc-desktop` → **Create**
6. In the dialog → **Download JSON**. Save it into the project root as **`credentials.json`**.
   - This is the file `GMAIL_CREDENTIALS_FILE` points at. It is a *client* secret (identifies the app), not a user token. Still, don't commit it.

That's all the console work. Everything else (`token.json`, `last_history_id.json`) is created automatically on first run.

### 2.5 What `credentials.json` contains

You don't edit it, but for reference a Desktop-app client secret looks like:

```json
{
  "installed": {
    "client_id": "1234567890-abc123.apps.googleusercontent.com",
    "project_id": "quoteflow-poc",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "GOCSPX-xxxxxxxxxxxxxxxx",
    "redirect_uris": ["http://localhost"]
  }
}
```

**Install dependencies** (already in `pyproject.toml` here — `uv sync`):
```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib fastapi "uvicorn[standard]" python-dotenv
```

---

## 3. Authentication code  (`gmail_trigger_poc/auth.py`)

Uses Google's current recommended pattern: credentials stored in plain JSON (`token.json`), not pickle.

```python
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as f:
            f.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)
```

In this repo the filenames and scopes come from `config.Settings` (env-driven) instead of literals, but the logic is identical.

**What happens on each run:**
- `token.json` exists and is valid → loads silently, no browser
- Exists but expired, refresh token present → refreshes silently in the background
- Doesn't exist, or refresh fails → opens browser for one-time OAuth consent, then saves the token

**Credentials ARE stored locally** — `token.json` sits in your project folder and persists across runs. Treat it like a password (don't commit to git). It contains the access token, refresh token, and the scopes it was issued for.

---

## 4. OAuth scopes — what `gmail.modify` actually grants

| Scope | Grants |
|---|---|
| `gmail.readonly` | Read only, no changes |
| **`gmail.modify`** ← used here | Read + label changes (mark read/unread, archive, trash). **No sending, no permanent delete.** |
| `gmail.send` | Send only — cannot read inbox |
| `gmail.compose` | Create/send/drafts, cannot read existing mail |
| `https://mail.google.com/` | Full access — everything |

**To add sending later** (for the "send quotation to client" step), add `gmail.send` to `GMAIL_SCOPES`. This requires deleting the existing `token.json` and re-authenticating — a token minted under the old scope list won't silently gain new permissions.

```
GMAIL_SCOPES=https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send
```

---

## 5. Polling loop (history-based)  (`gmail_trigger_poc/poller.py` + `gmail_client.py`)

**Fact-check note:** the naive approach polls `messages.list` every cycle (5 quota units/call, re-scans the query each time). This version uses `users.history.list` instead — 2 quota units/call, returns only the delta since the last checkpoint. This is Google's documented recommended pattern for keeping a local copy of a mailbox in sync.

```python
def poll_gmail(interval_seconds=20):
    service = get_gmail_service()
    start_history_id = get_start_history_id(service)   # saved checkpoint, or getProfile() on first run
    print("Polling started...")

    while True:
        try:
            resp = service.users().history().list(
                userId='me',
                startHistoryId=start_history_id,
                historyTypes=['messageAdded'],
            ).execute()

            for record in resp.get('history', []):
                for msg_added in record.get('messagesAdded', []):
                    msg_id = msg_added['message']['id']
                    email_data = get_message_body(service, msg_id)
                    print(f"New email from {email_data['from']}: {email_data['subject']}")
                    try:
                        result = process_email(email_data)     # direct in-process call — Step 2
                        print(f"Processed: {result}")
                        mark_as_read(service, msg_id)           # only mark read once processing succeeds
                    except Exception as proc_err:
                        print(f"Processing failed, will retry next poll: {proc_err}")
                        # deliberately NOT marking as read — retried automatically next cycle

            start_history_id = resp.get('historyId', start_history_id)
            save_history_id(start_history_id)

        except HttpError as e:
            if e.resp.status == 404:
                # historyId too old (records retained ~1 week) — re-baseline
                start_history_id = service.users().getProfile(userId='me').execute()['historyId']
            else:
                print(f"Poll error: {e}")

        time.sleep(interval_seconds)
```

Helpers (`gmail_client.py`): `get_start_history_id`, `save_history_id`, `get_message_body`
(decodes both simple and multipart MIME, prefers `text/plain`), `mark_as_read`.

**Wiring it into the single FastAPI process** (`gmail_trigger_poc/app.py`) — `process_email()` is
defined once, called two ways. Modern FastAPI uses a `lifespan` handler rather than the deprecated
`@app.on_event("startup")`:

```python
from contextlib import asynccontextmanager
import threading
from fastapi import FastAPI
from gmail_trigger_poc.pipeline import process_email
from gmail_trigger_poc.poller import poll_gmail

@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=poll_gmail, daemon=True).start()   # if ENABLE_POLLER
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/process-email")
def process_email_endpoint(gmail_message: dict):
    """Thin wrapper — same method the poller calls, kept reachable over HTTP
    for curl/Postman testing (Plan B §7) and any future non-poller trigger."""
    return process_email(gmail_message)
```

**Run it:**
```bash
uv run uvicorn gmail_trigger_poc.app:app --reload
# or the console script:
uv run gmail-trigger-poc
```
One command, one process — the poller thread starts automatically alongside the API server.
Set `ENABLE_POLLER=false` to run the HTTP API alone.

---

## 6. Code flow, step by step

1. **Process starts** → `lifespan` spawns the `poll_gmail()` daemon thread, which calls `get_gmail_service()`
2. **Authenticate** → loads or refreshes `token.json`; browser consent only on first-ever run
3. **Get baseline ID** → `get_start_history_id()` reads the saved checkpoint, or calls `getProfile()` on first run to mean "start watching from now"
4. **Call `history.list`** → asks Gmail "what changed since this checkpoint" — 2 quota units, delta only
5. **New messages found?** → if none, skip straight to the checkpoint save — no wasted API calls
6. **For each message** → `get_message_body()` fetches and decodes the full email (handles both simple and multipart MIME structures)
7. **`process_email()`** → called directly, in-process — the poller thread and the API server share the same Python process, so this is a plain function call, not a network request. This is the hook point for the QuoteFlow pipeline (Step 2 — not yet built). The same method is also reachable via `POST /process-email` as a thin wrapper
8. **`mark_as_read()`** → runs *only if* `process_email()` returns without raising, so a failure in processing leaves the email unread and it gets retried next poll (at-least-once delivery — `process_email()` should be safe to run twice on the same email)
9. **Save new `historyId`** → checkpoint written to disk before sleeping
10. **Sleep 20s → repeat** → if the gap since last run exceeds Gmail's history retention (~1 week, not guaranteed), a 404 triggers a re-baseline from `getProfile()`

```
Process starts
   │
   ▼
Authenticate (token.json)
   │
   ▼
Get baseline history ID
   │
   ▼
┌─────────────────────────┐
│ Call history.list        │◀───────────┐
└──────────┬───────────────┘            │
           ▼                            │
   New messages found? ──No──┐          │
           │ Yes              │         │
           ▼                  │         │
   For each: get body          │        │
   → process_email() (direct)   │       │
   → mark_as_read() on success   │      │
           │                    │       │
           ▼                    ▼       │
   Save new history ID ◀────────┘       │
           │                            │
           ▼                            │
   Sleep 20s ─────────────────────────┘
```

---

## 7. Setup checklist for the new test Gmail account

- [ ] Add the test account's email under OAuth consent screen → Test users
- [ ] Run the OAuth flow logged in as *that* account (not your personal Gmail)
- [ ] Confirm `token.json` was created and is scoped to the test account
- [ ] Keep `credentials.json` + `token.json` project-specific; use separate `GMAIL_TOKEN_FILE` names if a second account is added later
- [ ] Send a real test enquiry email to the account and confirm polling picks it up

---

## 8. What's next — Step 2

Not started yet. Fill in the body of `process_email()` (`gmail_trigger_poc/pipeline.py`) with the four pipeline stages, drawing on the extraction contract from `quoteflow_plan_B_without_extraction.md` §5:

```python
def process_email(email_data: dict) -> dict:
    result = extract_and_classify(email_data)   # classify + extract, Claude calls
    if result["scenario"] != "client_enquiry":
        return {"status": "logged", "scenario": result["scenario"]}
    # ... match against products/suppliers, draft quotation ...
    return {"status": "processed", "quotation_id": quotation_id}
```

The four stages:

1. **Classify** — Claude call: is this a genuine product enquiry or spam/internal?
2. **Extract** — Claude call: pull structured fields (product, quantity, specs, client info, urgency) into `email_threads.extracted_data_json`
3. **Match** — hybrid SQL (`supplier_products` exact lookups) + vector search (Chroma `products` collection) for fuzzy matching
4. **Draft** — generate the quotation, writing field-level tags (`AI-GENERATED` / `FROM-DATABASE` / `NEEDS-REVIEW`) into `quotation_field_status`

**Before wiring the live poller to it**, test `process_email()` standalone first — either call it directly in a Python shell, or hit `POST /process-email` with curl/Postman using a Gmail-shaped payload, per Plan B §7 step 4. Both paths exercise the exact same code, so passing one means the poller will work too.

---

## 9. Architect review — cross-checked against project POC docs

Reviewed against `quoteflow-flow-document.md`, `QuoteFlow_Minimal_Version_1.md`, `quoteflow_plan_B_without_extraction.md`, and `quoteflow_schema_without_extraction.sql`.

### 9.1 Decision: single shared method, called directly — not a network hop
An earlier revision had the poller `POST`ing to `/process-email` over `http://localhost` as if it were an external service. That's unnecessary overhead for a single local process — `QuoteFlow_Minimal_Version_1.md` is explicit that this should run as "one backend process," and `quoteflow_plan_B_without_extraction.md` §5 only requires that `/process-email` be *reachable* for curl/Postman testing, not that the poller itself use HTTP. **Current design:** `process_email()` is defined once (`pipeline.py`); the poller thread calls it directly as a function, and `/process-email` stays as a thin FastAPI wrapper around that same method. The poller runs as a daemon thread started on FastAPI's `lifespan`, so `uvicorn gmail_trigger_poc.app:app` is the only command needed to run the whole backend.

### 9.2 Open item for Step 2: payload shape
Since the poller and the HTTP route both feed the same `process_email(email_data)` function, the shape is decided by whichever gets built first — this doc's poller already produces `{id, thread_id, from, to, subject, date, body}` (`gmail_client.get_message_body()`). Recommendation: keep that as the canonical shape `process_email()` expects; if `/process-email` is ever called externally with a raw Gmail API message object instead, add a small adapter in the route handler rather than changing the shared function's contract.

### 9.3 Confirmed correct, no changes needed
- **OAuth flow, `token.json` pattern, scopes table** — matches Google's current Gmail API documentation; the account setup checklist (§2, §7) matches what a fresh test-account run actually requires.
- **`gmail.modify` scope choice** — sufficient for the read + mark-as-read behavior used throughout; sending support is correctly called out as a separate, later scope addition (§4).
- **`history.list` polling design** — matches `quoteflow-flow-document.md`'s "Email trigger: ... periodic polling (Gmail API)" and `QuoteFlow_Minimal_Version_1.md`'s explicit call for polling over Pub/Sub for the POC. The upgrade from `messages.list` to `history.list` doesn't change this decision, only the efficiency of the implementation.
- **`email_threads` schema alignment** — the poller's output (`gmail_message_id`, `from_email`/`to_email`, `subject`, `body_text`, `direction`) maps cleanly onto the `email_threads` table columns in `quoteflow_schema_without_extraction.sql`; no field mismatches.
- **At-least-once delivery via deferred `mark_as_read()`** — consistent with the schema's `processed_at` column being nullable and separate from receipt.

### 9.4 Not addressed by this document (by design)
Sending the finished quotation back to the client (`gmail.send`, MIME-encoding, `messages.send()`) and the `/process-email` handler internals are Step 2+ work, intentionally out of scope here. This document covers the inbound polling leg only.
