# gmail-trigger-poc

Local POC for **QuoteFlow** (v2SQ — supplier-quote outreach). A Python process
polls a Gmail inbox and hands every new email to `process_email()`, which
classifies it, extracts the enquiry, matches suppliers, and drafts one outreach
email per matched supplier. Runs entirely on localhost — **polling**
(`users.history.list`), **SQLite**, embedded **ChromaDB**; no webhook or domain.

- **Step 1** — Gmail OAuth + history-based polling. ✅
- **Step 2** — `classify → extract → match → draft` pipeline + REST API. ✅
  Sending outreach back out (`gmail.send`) and client-facing quotations are out
  of scope for this version (integration guide §9.4).

```
Gmail inbox (test account)
      │  poll every 20s (background daemon thread)
      ▼
Single FastAPI process  (uvicorn gmail_trigger_poc.app:app)
      ├── poll_gmail() thread ──┐
      │                         ▼
      │        process_email(email) ─ classify+extract (LLM)
      │                             ─ match: Chroma products → SQL suppliers → rerank
      │                             ─ draft 1 outreach email per supplier ≥ threshold (LLM)
      │                             → supplier_quote_requests rows  (status 'draft')
      │                         ▲
      └── POST /process-email ──┘  thin wrapper for curl/Postman
              GET  /enquiry/{id}/supplier-drafts     GET /enquiries  /suppliers
              PATCH/POST /supplier-quote-requests/{id}[/send]
```

## Layout

| Path | Purpose |
|---|---|
| `src/gmail_trigger_poc/config.py` | Settings loaded from env / `.env` |
| `src/gmail_trigger_poc/logging_setup.py` | Coloured `[TAG]` logging, `configure_logging()` |
| `src/gmail_trigger_poc/auth.py` | OAuth — `get_gmail_service()` (JSON token, not pickle) |
| `src/gmail_trigger_poc/gmail_client.py` | history checkpoint, message fetch, mark-as-read |
| `src/gmail_trigger_poc/poller.py` | `poll_gmail()` background loop |
| `src/gmail_trigger_poc/schema.sql` | canonical v2SQ SQLite schema (shipped verbatim, +1 column) |
| `src/gmail_trigger_poc/db.py` | SQLite connect + `init_db()` |
| `src/gmail_trigger_poc/seed.py` | one-time backfill load + product embedding |
| `src/gmail_trigger_poc/vectorstore.py` | ChromaDB client + embedders (MiniLM / offline hash) |
| `src/gmail_trigger_poc/matching.py` | vector search → SQL supplier join → rerank |
| `src/gmail_trigger_poc/llm.py` | classify+extract & draft — Claude or offline stub |
| `src/gmail_trigger_poc/repository.py` | clients / email_threads / supplier_quote_requests SQL |
| `src/gmail_trigger_poc/pipeline.py` | `process_email()` — the orchestration |
| `src/gmail_trigger_poc/app.py` | FastAPI app + poller thread on startup |
| `data/quoteflow_seed.json` | seed dataset (15 clients / 18 suppliers / 26 products / 104 emails) |
| `docs/step2-pipeline.md` | what Step 2 built + validated + deviations from the plans |
| `plans/` | the QuoteFlow spec set (flow doc, Plan B, schema, UI, minimal version) |

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
uv sync                              # install (pulls anthropic + chromadb)
cp .env.example .env                 # then edit
uv run python -m gmail_trigger_poc.seed   # build quoteflow.db + chroma_data/ from the seed set
uv run gmail-trigger-poc             # or: uv run uvicorn gmail_trigger_poc.app:app --reload
```

`seed` is a one-time backfill (re-run any time; `--keep` to load without resetting).
The Gmail poller also needs `credentials.json` (Google Cloud OAuth client, Desktop
app) in this folder — first run opens a browser once for consent, then caches
`token.json`. Set `ENABLE_POLLER=false` to run the API alone.

**Dashboard:** open <http://127.0.0.1:8000/> — a single-page "Supplier Outreach
Desk" ([src/gmail_trigger_poc/web/index.html](src/gmail_trigger_poc/web/index.html),
vanilla JS, served by the API) wired to every endpoint: live enquiry list, the
stacked supplier-card builder with per-field tags + live body re-render, Save
(`PATCH`) / Send (`POST …/send`), a supplier directory, and a **Simulate email**
button that `POST`s `/process-email` — the same call the poller makes — so the
whole flow is demoable without sending real mail.

```bash
curl localhost:8000/health
# drive the pipeline with a Gmail-shaped payload:
curl -X POST localhost:8000/process-email -H 'content-type: application/json' -d '{
  "id":"m1","thread_id":"t1","from":"john@apex-mfg.com",
  "subject":"Enquiry: Stainless Steel Bolts M10x50 - 5,000 pieces",
  "body":"- Product: Stainless Steel Bolts M10x50\n- Quantity required: 5,000 pieces\n- Preferred delivery terms: FOB Shanghai\n- Specifications: Grade A2-70, DIN 933\n- Delivery location: Ohio, USA"}'
# -> {"status":"drafted","email_thread_id":N,"supplier_quote_request_ids":[...],...}

curl localhost:8000/enquiry/N/supplier-drafts
curl -X POST localhost:8000/supplier-quote-requests/1/send -d '{}'   # mark-only, no gmail.send
```

### Working without API keys

Everything runs offline out of the box: `LLM_PROVIDER=auto` falls back to a
deterministic **stub** when no `ANTHROPIC_API_KEY` (or `ant auth` profile) is
present, and `EMBEDDING_PROVIDER=auto` falls back to an offline hash embedder if
MiniLM can't download. To go live, add credentials and re-seed:

```bash
export ANTHROPIC_API_KEY=sk-ant-...          # real Claude for classify/extract/draft
export EMBEDDING_PROVIDER=chroma_default      # real MiniLM product embeddings
uv run python -m gmail_trigger_poc.seed       # re-embed under the new provider
```

## Tests

```bash
uv run pytest
```

Tests pin `EMBEDDING_PROVIDER=hash` + `LLM_PROVIDER=stub` and seed a throwaway
SQLite/Chroma store under a temp dir — no network, no model download, no key.

## Environment variables

See `.env.example` — every value has a default. Step 1 (Gmail/OAuth/logging)
vars are documented in `docs/gmail-integration-guide.md` §2; Step 2 (DB, Chroma,
embeddings, LLM, matching) vars are in `.env.example` and `docs/step2-pipeline.md`.
