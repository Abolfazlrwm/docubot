# Architecture deep dive

This document explains the design decisions behind DocuBot: how a document
becomes searchable, how a question becomes a cited answer, and why the pieces
are shaped the way they are.

## The big picture

```
                    ┌──────────── Docker network ────────────┐
                    │                                        │
  browser ──► web (Django/DRF + gunicorn) ──► PostgreSQL 16   │
     │                   │        cache ► Redis               │
     │                   │ enqueue ► Redis (Celery broker)    │
  demo UI                └──► worker (Celery) ──► Postgres    │
                    │                                        │
                    └────────────────────────────────────────┘
```

Three Django apps carry the domain:

| App | Responsibility |
| --- | --- |
| `apps.users` | Custom `User` (unique lowercase email), registration, JWT |
| `apps.documents` | `Document` + `Chunk`, upload validation, extraction, chunking, the Celery pipeline |
| `apps.chat` | `Conversation` + `Message`, pgvector retrieval, RAG orchestration, JSON + SSE APIs |
| `apps.common` | Error taxonomy, provider abstractions (embeddings/LLM), throttling, health |

Business logic lives in `services.py` modules; views, serializers and Celery
tasks are deliberately thin. That keeps every rule testable without HTTP or a
broker, and it is why the test suite is 243 tests with an 85% coverage floor.

## Documents: from upload to vectors

### 1. Upload & validation (`apps/documents/validators.py`)

- Extension allow-list (`.pdf`, `.txt`) — cheap, checked first.
- Size cap (`MAX_UPLOAD_SIZE`).
- **Content** checks, because extensions and content types are client-controlled:
  PDFs must start with the `%PDF-` magic bytes, TXT must decode as UTF-8.
- `strip_unsafe_basename` removes any path component; `document_upload_to` then
  stores the file under `documents/<user_id>/<uuid>_<name>`, so paths are
  unpredictable and there is no download endpoint that would expose them.

### 2. The async pipeline (`services.py` + `tasks.py`)

```
POST /documents/            worker                            Postgres
      │                        │                                  │
      ├─ validate + store      │                                  │
      ├─ Document(pending)     │                                  │
      └─ on_commit ── delay ─► claim: pending→processing          │
                               ├─ extract text (pypdf / UTF-8)    │
                               ├─ chunk (1000c, 200 overlap)      │
                               ├─ embed (batches of 32)           │
                               └─ delete old chunks + bulk_create ─► Chunk(vector 384)
                                  ready  (or failed + message)
```

Design decisions worth copying:

- **Enqueue after commit** (`transaction.on_commit`): the worker can never see
  an uncommitted row.
- **Idempotent claim step.** `select_for_update` moves `pending → processing`
  and stamps `processing_started_at`. With `acks_late=True` and prefetch=1, a
  crashed worker's task is redelivered; the stale-claim check
  (`PROCESSING_STALE_AFTER_SECONDS`) lets the retry take over instead of
  deadlocking behind a dead worker.
- **Permanent vs transient errors.** `PermanentProcessingError` (corrupt PDF,
  encrypted PDF, no extractable text) marks the document `failed` with a clear
  message. `TransientProcessingError` (network, model load) releases the claim
  and lets Celery's autoretry (backoff + jitter, max 3) try again.
- **Chunk replacement is atomic.** Re-running a task deletes old chunks and
  bulk-creates new ones in one transaction; the unique
  `(document, chunk_index)` constraint is the backstop. Reprocessing can never
  duplicate.

### 3. Chunking (`chunking.py`)

A pure recursive splitter with no I/O: paragraphs → lines → sentences → words →
hard cut, keeping trailing separators so merged chunks stay readable, and
packing with overlap (default 1000/200 chars). Sentence separators include
Persian/Arabic punctuation (`؟ ؛ ۔`) because the default embedding model is
multilingual and Persian documents are a first-class use case.

## Chat: from question to cited answer

### Retrieval (`apps/chat/retrieval.py`)

One SQL query does everything:

```sql
SELECT ..., (embedding <=> $query) AS distance
FROM documents_chunk
JOIN documents_document ON ...
WHERE owner = :me AND status = 'ready'   -- tenancy + readiness INSIDE the scan
ORDER BY distance
LIMIT :top_k;
```

- **Tenancy is part of the scan**, not a post-filter: injected `document_ids`
  can only narrow the caller's own set, never widen it.
- **HNSW and selective filters.** HNSW walks the graph first and applies SQL
  filters to candidates afterwards, so a selective `WHERE` can legitimately
  return fewer rows than asked. On pgvector ≥ 0.8 DocuBot enables
  `hnsw.iterative_scan = relaxed_order` inside the retrieval transaction; on
  older versions it raises `hnsw.ef_search` instead. The extension version is
  looked up once per process.
- `RETRIEVAL_MIN_SCORE` (cosine similarity, default 0.3) drops noise before the
  LLM ever sees it.

### RAG orchestration (`apps/chat/services.py`)

- Context blocks are numbered `[1] … [n]` between `CONTEXT START/END` markers.
- The system prompt enforces: answer only from context, cite sources as `[1]`,
  reply in the question's language, and treat context as **untrusted data** —
  instructions found inside documents must be reported, not obeyed.
- Conversation history (last `CHAT_HISTORY_MESSAGES`) is included for follow-ups.
- **The LLM call never holds a DB transaction open.** Messages persist in a
  single transaction *after* the answer exists, so a slow LLM can't hold row
  locks.
- `sources` is a JSON **snapshot** (title, chunk index, score, 300-char
  snippet), deliberately not a FK: chunks are deleted and recreated on
  reprocessing, and chat history must survive that.

### Streaming (`/conversations/{id}/messages/stream/`)

Server-Sent Events over `StreamingHttpResponse`:

```
event: sources   data: {"sources": [...]}     # what was retrieved, numbered
event: delta     data: {"text": "..."}        # repeated, token-ish chunks
event: done      data: {"message_id": ...}    # turn persisted, usage included
event: error     data: {"detail": "..."}      # LLM failure; nothing persisted
```

- `X-Accel-Buffering: no` and `Cache-Control: no-cache` keep proxies honest.
- Providers implement `generate_stream(system, messages, meta)`: the OpenAI
  parser reads `data:` chunks (and optional usage-only final chunks), the
  Anthropic parser reads `message_start` / `content_block_delta` /
  `message_delta`, and the base class degrades gracefully by chunking a plain
  `generate()` for backends without streaming.
- A client disconnect mid-stream persists the tokens already sent
  (`GeneratorExit` path), so the user keeps their partial answer.

## AI provider abstractions (`apps/common/ai/`)

Both embeddings and LLMs sit behind small ABCs with a deterministic `fake`
implementation. This is what makes the test suite hermetic and offline demos
possible:

| Provider | Embeddings | LLM |
| --- | --- | --- |
| `fake` | hashed bag-of-words, normalized (related texts are close) | echoes context, cites `[1]` |
| `local` | fastembed ONNX, loaded once per process | — |
| `openai` | any `/embeddings` endpoint | any `/chat/completions` endpoint |
| `anthropic` | — | `/v1/messages` |

Error contracts mirror each other (`Permanent/TransientProcessingError` vs
`LLMPermanentError/LLMTransientError`) so retry policy is decided by
classification, not by call sites. Log lines carry lengths and ids only — never
keys, prompts or document contents.

## Cross-cutting concerns

- **Rate limiting** (`apps/common/throttling.py`): DRF `ScopedRateThrottle`
  scopes (`auth`, `chat`, `search`, `upload`) with env-tunable rates. The
  `WriteMethodsRateThrottle` subclass meters only unsafe methods, so reading
  history never burns the chat bucket.
- **Health** (`/api/v1/health/`): public, cheap, JSON. DB and cache failures
  return 503 (fatal for a probe); a missing Celery worker is `degraded` but
  keeps the API 200. The Docker image's `HEALTHCHECK` uses it.
- **Settings**: `base.py` holds everything env-driven; `dev.py`/`prod.py` differ
  only in DEBUG/SECURE settings, so prod drift is visible in one file.
- **Static files**: WhiteNoise serves the demo UI's assets in production;
  `STATICFILES_DIRS` picks up the root `static/` folder.

## Testing strategy

| Layer | How |
| --- | --- |
| Pure functions (chunking, extraction, validators, providers) | unit tests, no DB |
| Services (processing, retrieval, RAG) | DB-backed tests with fake providers |
| API contracts | DRF `APIClient`, real JWT flow |
| Isolation & tenancy | dedicated owner-leak tests |
| Performance regressions | `django_assert_max_num_queries` N+1 guards |
| SSE | streaming content parsed and asserted event-by-event |
| CI | same suite against live pgvector 16 + Redis 7 services |
