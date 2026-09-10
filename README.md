# gmail-trigger-poc

Local POC for **QuoteFlow** (v2SQ — supplier-quote outreach). A Python process
polls a Gmail inbox and hands every new email to `process_email()`, which
records it (`received`), classifies it, extracts the enquiry, matches suppliers,
and drafts **one shared outreach email** the operator sends to any subset of the
matched suppliers in a single click. Runs entirely on localhost — **polling**
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
      │        process_email(email) ─ insert email_threads row  (status 'new' / 'received')
      │                             ─ classify+extract (LLM)         → 'processing' / 'extracted'
      │                             ─ match: Chroma products → SQL suppliers → rerank
      │                             ─ snapshot matched_suppliers_json → 'matched'
      │                             ─ draft ONE shared outreach email (LLM)  → 'drafting'
      │                             → email_threads.draft_* + one supplier_quote_requests
      │                               send-record per matched supplier  (status 'draft')
      │                         ▲
      └── POST /process-email ──┘  thin wrapper for curl/Postman
              GET   /enquiry/{id}/supplier-drafts    GET /enquiries  /suppliers
              PATCH /enquiry/{id}/draft              edit the one shared draft
              POST  /enquiry/{id}/send  {supplier_ids:[…]}   send it to a chosen subset
              PATCH/POST /supplier-quote-requests/{id}[/send]   (per-row, kept for back-compat)
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

`seed` is a one-time backfill (re-run any time; `--keep` to load without
resetting; `--demo` also overlays `data/demo_enquiries.json` — six enquiries
frozen at different lifecycle stages, IDs `9000+`, for walkthroughs).

**Enquiry lifecycle** (`email_threads.status` / `pipeline_stage`, surfaced on the
Desk as badges + a processing trace): `new`/`received` (row written the instant
the email lands, before any LLM call) → `processing`
(`extracted`→`matched`→`drafting`) → `drafted`, or `logged` for a non-enquiry.
The live pipeline writes these progressively, so the Desk shows "Enquiry
received" in real time and then a real "Processing" badge while drafting. The
matched suppliers are snapshotted (`matched_suppliers_json`) so the trace names
them after a refresh. "Fully sent" is computed (`all_sent`: every
`supplier_quote_requests` row sent), never a stored status.
The Gmail poller also needs `credentials.json` (Google Cloud OAuth client, Desktop
app) in this folder — first run opens a browser once for consent, then caches
`token.json`. Set `ENABLE_POLLER=false` to run the API alone.

### Dashboard

Open <http://127.0.0.1:8000/> — the **Supplier Outreach Desk**: live enquiry
list, the **one shared outreach editor** (subject + `{{supplier_name}}`-tokened
body + per-field `AI-GENERATED` / `FROM-DATABASE` / `NEEDS-REVIEW` tags with live
re-render — no reference price), a **checkbox list of the matched suppliers**, and
a single **Send to N suppliers** button (`POST /enquiry/{id}/send`) that mails the
same text to every ticked supplier with their own name filled in. Plus a supplier
directory and a **Simulate email** button that `POST`s `/process-email` — the same
call the poller makes — so the whole flow is demoable without sending real mail.

```bash
cd frontend && npm install && npm run build    # -> frontend/dist/, served by the API at /
```

- **React + Vite** app in [frontend/](frontend/) (`src/App.jsx` + components).
  `app.py` serves `frontend/dist/` at `/` when it exists.
- Iterating on the UI: `npm run dev` (port 5173, proxies the API to `:8000`).
- No Node? The API falls back to a dependency-free vanilla-JS build of the same
  screen at [src/gmail_trigger_poc/web/index.html](src/gmail_trigger_poc/web/index.html).

```bash
curl localhost:8000/health
# drive the pipeline with a Gmail-shaped payload:
curl -X POST localhost:8000/process-email -H 'content-type: application/json' -d '{
  "id":"m1","thread_id":"t1","from":"john@apex-mfg.com",
  "subject":"Enquiry: Stainless Steel Bolts M10x50 - 5,000 pieces",
  "body":"- Product: Stainless Steel Bolts M10x50\n- Quantity required: 5,000 pieces\n- Preferred delivery terms: FOB Shanghai\n- Specifications: Grade A2-70, DIN 933\n- Delivery location: Ohio, USA"}'
# -> {"status":"drafted","email_thread_id":N,"supplier_quote_request_ids":[...],...}

curl localhost:8000/enquiry/N/supplier-drafts        # shared draft + matched-supplier checklist
curl -X PATCH localhost:8000/enquiry/N/draft -H 'content-type: application/json' \
  -d '{"draft_subject":"RFQ - bolts","fields":[…]}'  # edit the one shared draft
curl -X POST localhost:8000/enquiry/N/send -H 'content-type: application/json' \
  -d '{"supplier_ids":[1,2]}'                        # mark-only send to a chosen subset, no gmail.send
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

### Local LLM via Ollama

For a real model with no key and no network, point the pipeline at a local
[Ollama](https://ollama.com) server:

```bash
ollama pull llama3.1:8b                       # one-time download (~4.7 GB)
export LLM_PROVIDER=ollama                    # OLLAMA_MODEL defaults to llama3.1:8b
uv run uvicorn gmail_trigger_poc.app:app
```

Same prompts as the Claude path; the model is constrained to JSON via Ollama's
`format: "json"`. `OLLAMA_HOST` (default `http://localhost:11434`) and
`OLLAMA_MODEL` are the knobs. Embeddings are independent — `EMBEDDING_PROVIDER`
still selects MiniLM / hash.

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
