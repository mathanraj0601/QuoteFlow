# QuoteFlow — Gmail Integration Guide (v2SQ)

**v2SQ note:** §1–7 below (OAuth, polling, `history.list` sync) are unchanged — the inbound leg doesn't care what Step 2 produces. What changed is Step 2's target output: instead of drafting one client-facing quotation, it now drafts one outreach email per matched supplier. See §8.

**Status:** Step 1 complete (Gmail polling + auth), architect-reviewed and aligned with the project's POC plan. Step 2 (`/process-email` pipeline) not started yet.
**Fact-checked against:** Google's official Gmail API Python quickstart and sync guide (developers.google.com/workspace/gmail/api).
**Cross-checked against:** `quoteflow-flow-document.md`, `QuoteFlow_Minimal_Version_1.md`, `quoteflow_plan_B_without_extraction.md`, `quoteflow_schema_without_extraction.sql`. See §9 for the review findings.

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
Single FastAPI process (uvicorn app:app)
      │
      ├── poll_gmail() thread ──┐
      │                         ▼
      │                  process_email()  ← Step 2, not built yet — one shared method
      │                         ▲
      └── POST /process-email ──┘  (thin wrapper, for curl/Postman + external triggers)
```

**Architecture note (revised in this update):** `process_email()` is one method. The poller calls it directly, in-process — no HTTP hop to itself. The `/process-email` route is kept as a thin wrapper around the same method, purely so it can still be tested standalone with curl/Postman (per Plan B §7) or triggered by something other than the local poller later. See §9.1.

---

## 2. One-time setup (Google Cloud Console)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Create or select a project
3. **APIs & Services → Library** → search "Gmail API" → Enable
4. **APIs & Services → OAuth consent screen** → add your new test Gmail account under **Test users**
   - Important: apps that aren't published/verified are capped at **100 test users** — irrelevant at our scale, but good to know it's a hard limit if this ever grows
5. **APIs & Services → Credentials** → Create Credentials → OAuth client ID → Application type: **Desktop app**
6. Download the JSON, save it as `credentials.json` in the project folder

**Install dependencies:**
```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

---

## 3. Authentication code

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

**To add sending later** (for the "send outreach to supplier" step, v2SQ), add `gmail.send` to `SCOPES`. This requires deleting the existing `token.json` and re-authenticating — a token minted under the old scope list won't silently gain new permissions.

```python
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send'
]
```

---

## 5. Polling loop (history-based)

**Fact-check note:** the original naive approach polled `messages.list` every cycle (5 quota units/call, re-scans the query each time). This version uses `users.history.list` instead — 2 quota units/call, returns only the delta since the last checkpoint. This is Google's documented recommended pattern for keeping a local copy of a mailbox in sync.

```python
import os, json, time, base64
import threading
from fastapi import FastAPI

HISTORY_FILE = 'last_history_id.json'
app = FastAPI()

def get_start_history_id(service):
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)['historyId']
    profile = service.users().getProfile(userId='me').execute()
    return profile['historyId']

def save_history_id(history_id):
    with open(HISTORY_FILE, 'w') as f:
        json.dump({'historyId': history_id}, f)

def get_message_body(service, msg_id):
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    payload = msg['payload']
    headers = {h['name']: h['value'] for h in payload['headers']}

    body = ""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                break
    elif 'body' in payload and 'data' in payload['body']:
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')

    return {
        'id': msg_id,
        'thread_id': msg['threadId'],
        'from': headers.get('From'),
        'subject': headers.get('Subject'),
        'body': body
    }

def mark_as_read(service, msg_id):
    service.users().messages().modify(
        userId='me', id=msg_id, body={'removeLabelIds': ['UNREAD']}
    ).execute()

def poll_gmail(interval_seconds=20):
    service = get_gmail_service()
    start_history_id = get_start_history_id(service)
    print("Polling started...")

    while True:
        try:
            resp = service.users().history().list(
                userId='me',
                startHistoryId=start_history_id,
                historyTypes=['messageAdded']
            ).execute()

            for record in resp.get('history', []):
                for msg_added in record.get('messagesAdded', []):
                    msg_id = msg_added['message']['id']
                    email_data = get_message_body(service, msg_id)
                    print(f"New email from {email_data['from']}: {email_data['subject']}")

                    try:
                        result = process_email(email_data)  # direct in-process call — Step 2
                        print(f"Processed: {result}")
                        mark_as_read(service, msg_id)  # only mark read once processing succeeds
                    except Exception as proc_err:
                        print(f"Processing failed, will retry next poll: {proc_err}")
                        # deliberately NOT marking as read — retried automatically next cycle

            start_history_id = resp.get('historyId', start_history_id)
            save_history_id(start_history_id)

        except Exception as e:
            if '404' in str(e):
                # historyId too old (records typically retained ~1 week) — re-baseline
                profile = service.users().getProfile(userId='me').execute()
                start_history_id = profile['historyId']
            else:
                print(f"Poll error: {e}")

        time.sleep(interval_seconds)
```
*(continues below — `poll_gmail()` is wired up, not run standalone)*

**Wiring it into the single FastAPI process** — `process_email()` is defined once, called two ways:

```python
def process_email(email_data: dict) -> dict:
    """The actual pipeline: classify -> extract -> match -> draft (Step 2).
    Called directly by the poller, and wrapped below for external/manual testing."""
    # Step 2 logic goes here
    ...
    return {"status": "processed", "quotation_id": None}

@app.post("/process-email")
def process_email_endpoint(gmail_message: dict):
    """Thin wrapper — same method the poller calls, kept reachable over HTTP
    for curl/Postman testing (Plan B §7) and any future non-poller trigger."""
    return process_email(gmail_message)

@app.on_event("startup")
def start_poller():
    threading.Thread(target=poll_gmail, daemon=True).start()
```

**Run it:**
```bash
uvicorn app:app --reload
```
One command, one process — the poller thread starts automatically alongside the API server. No separate `python poll_gmail.py` process needed anymore.

---

## 6. Code flow, step by step

1. **Script starts** → `poll_gmail()` runs, calls `get_gmail_service()`
2. **Authenticate** → loads or refreshes `token.json`; browser consent only on first-ever run
3. **Get baseline ID** → `get_start_history_id()` reads the saved checkpoint, or calls `getProfile()` on first run to mean "start watching from now"
4. **Call `history.list`** → asks Gmail "what changed since this checkpoint" — 2 quota units, delta only
5. **New messages found?** → if none, skip straight to step 9 — no wasted API calls
6. **For each message** → `get_message_body()` fetches and decodes the full email (handles both simple and multipart MIME structures)
7. **`process_email()`** → called directly, in-process — the poller thread and the API server share the same Python process, so this is a plain function call, not a network request. This is the hook point for the QuoteFlow pipeline (Step 2 — not yet built). The same method is also reachable via `POST /process-email` as a thin wrapper, purely for curl/Postman testing or a future non-poller trigger
8. **`mark_as_read()`** → runs *only if* `process_email()` returns without raising, so a failure in processing leaves the email unread and it gets retried next poll (at-least-once delivery — `process_email()` should be safe to run twice on the same email)
9. **Save new `historyId`** → checkpoint written to disk before sleeping
10. **Sleep 20s → repeat** → loop continues; if the gap since last run exceeds Gmail's history retention (~1 week, not guaranteed), a 404 triggers a re-baseline from `getProfile()`

```
Script starts
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
- [ ] Keep `credentials.json` + `token.json` project-specific; use separate token filenames if a second account is added later
- [ ] Send a real test enquiry email to the account and confirm polling picks it up

---

## 8. What's next — Step 2 (v2SQ)

Not started yet. Fill in the body of `process_email()` (already scaffolded in §5/§9.1) with the four pipeline stages, drawing on the extraction contract from `quoteflow_plan_B_without_extraction.md` §5:

```python
def process_email(email_data: dict) -> dict:
    result = extract_and_classify(email_data)   # classify + extract, Claude calls
    if result["scenario"] != "client_enquiry":
        return {"status": "logged", "scenario": result["scenario"]}
    # ... match against products/suppliers, draft ONE outreach email PER matched supplier ...
    return {"status": "drafted", "supplier_quote_request_ids": draft_ids}
```

The four stages:

1. **Classify** — Claude call: is this a genuine product enquiry or spam/internal?
2. **Extract** — Claude call: pull structured fields (product, quantity, specs, client info, urgency) into `email_threads.extracted_data_json`
3. **Match** — hybrid SQL (`supplier_products` exact lookups) + vector search (Chroma `products` collection) for fuzzy matching; keep every supplier above the match-score threshold (default 0.65), not just the top one
4. **Draft (v2SQ)** — for each supplier kept in step 3, one Claude call drafting a quote-request email to that supplier (asking for pricing/MOQ/lead time against the client's requirement). The call returns a `body_template` (with `{{field_key}}` tokens) plus a structured `fields` list — each field carrying its value and a tag (`AI-GENERATED` / `FROM-DATABASE` / `NEEDS-REVIEW`) — written to `supplier_quote_requests.draft_body_template` / `draft_fields_json`, with the fully-substituted result cached in `draft_body`. This is what lets the UI's field edits regenerate the literal email text live, instead of showing fields next to a body that doesn't actually reflect them. Drafting a client-facing quotation is deferred — not part of this stage.

**Before wiring the live poller to it**, test `process_email()` standalone first — either call it directly in a Python shell, or hit `POST /process-email` with curl/Postman using a Gmail-shaped payload, per Plan B §7 step 4. Both paths exercise the exact same code, so passing one means the poller will work too.

This is the point where today's Gmail plumbing connects to the rest of the QuoteFlow schema already in place.

---

## 9. Architect review — cross-checked against project POC docs

Reviewed against `quoteflow-flow-document.md`, `QuoteFlow_Minimal_Version_1.md`, `quoteflow_plan_B_without_extraction.md`, and `quoteflow_schema_without_extraction.sql`.

### 9.1 Decision: single shared method, called directly — not a network hop
An earlier revision of this doc had the poller `POST`ing to `/process-email` over `http://localhost` as if it were an external service. That's unnecessary overhead for a single local process — `QuoteFlow_Minimal_Version_1.md` is explicit that this should run as "one backend process," and `quoteflow_plan_B_without_extraction.md` §5 only requires that `/process-email` be *reachable* for curl/Postman testing, not that the poller itself use HTTP. **Current design:** `process_email()` is defined once; the poller thread calls it directly as a function, and `/process-email` stays as a thin FastAPI wrapper around that same method — satisfying both docs at once. The poller runs as a daemon thread started on FastAPI's `startup` event, so `uvicorn app:app` is the only command needed to run the whole backend.

### 9.2 Open item for Step 2: payload shape
Since the poller and the HTTP route now both feed the same `process_email(email_data)` function, the input shape is effectively decided by whichever gets built first — this doc's poller already produces `{id, thread_id, from, subject, body}` (§5, `get_message_body()`). Recommendation: keep that as the canonical input shape `process_email()` expects; if `/process-email` is ever called externally with a raw Gmail API message object instead, add a small adapter in the route handler rather than changing the shared function's contract.

**v2SQ addition — output shape:** `process_email()` now returns `{"status": "drafted", "supplier_quote_request_ids": [...]}` for a `client_enquiry`, instead of a single `quotation_id`. See `quoteflow_plan_B_without_extraction.md` §5 for the full return shape used by the UI to fetch and render the supplier cards.

### 9.3 Confirmed correct, no changes needed
- **OAuth flow, `token.json` pattern, scopes table** — matches Google's current Gmail API documentation and the account setup checklist (§2, §7) matches what a fresh test-account run actually requires.
- **`gmail.modify` scope choice** — sufficient for the read + mark-as-read behavior used throughout; sending support is correctly called out as a separate, later scope addition (§4).
- **`history.list` polling design** — matches `quoteflow-flow-document.md`'s "Email trigger: ... periodic polling (Gmail API)" and `QuoteFlow_Minimal_Version_1.md`'s explicit call for polling over Pub/Sub for the POC ("Webhooks need a public HTTPS endpoint; polling works entirely on localhost, zero setup"). The upgrade from `messages.list` to `history.list` doesn't change this decision, only the efficiency of the implementation.
- **`email_threads` schema alignment** — the poller's output (`gmail_message_id`, `from_email`/`to_email`, `subject`, `body_text`, `direction`) maps cleanly onto the `email_threads` table columns in `quoteflow_schema_without_extraction.sql`; no field mismatches.
- **At-least-once delivery via deferred `mark_as_read()`** — consistent with the schema's `processed_at` column being nullable and separate from receipt, i.e. the design already assumes "received" and "processed" are distinct states.

### 9.4 Not addressed by this document (by design)
Sending finished supplier outreach drafts (`gmail.send`, MIME-encoding, `messages.send()`, one call per supplier draft) and the `/process-email` handler internals are Step 2+ work, intentionally out of scope here. This document covers the inbound polling leg only. **v2SQ note:** sending a quotation back to the client is not just "not addressed here" — it's out of scope for the whole project version, not only this document.
