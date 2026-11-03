# DocuChat

A Django REST backend for a RAG knowledge-base application: authenticated users upload
documents (PDF/TXT) that are processed in the background — text extraction, chunking
and embedding — and stored as vectors in Postgres (pgvector), ready for retrieval.
Chat/Q&A over the documents arrives in a later phase.

## Stack

- Django 5 + Django REST Framework
- PostgreSQL (pgvector) for documents, chunks and embedding vectors
- Redis: cache, Celery broker and results
- Celery worker: async processing pipeline
- SimpleJWT (access 30 min / refresh 7 days, rotation + blacklist)
- fastembed (ONNX) local multilingual embeddings — `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, supports Persian)
- django-filter, drf-spectacular (OpenAPI 3 + Swagger UI)
- pytest / pytest-django / factory-boy / pytest-cov, ruff

## Architecture: upload → Celery → embed

```
POST /api/v1/documents/          worker (celery)                Postgres (pgvector)
      │                                │                              │
      ├─ validate + store file         │                              │
      ├─ Document(status=pending)      │                              │
      └─ on_commit ── enqueue ───────► receive task                   │
                                       ├─ claim: pending→processing   │
                                       ├─ extract text (pypdf/UTF-8)  │
                                       ├─ chunk (1000 chars, 200 overlap)
                                       ├─ embed (fastembed, batches of 32)
                                       └─ delete old chunks + bulk_create
                                          status=ready ─────────────► Chunk(embedding vector(384))
                                          (permanent error → failed + message)
                                          (transient error → retry ×3, backoff+jitter)
```

- Documents with no extractable text (e.g. scanned PDFs) end `failed` with a clear message.
- `POST /documents/{id}/reprocess/` re-queues a failed document.
- Running the task twice never duplicates chunks (unique `(document, chunk_index)` + delete-then-create in one transaction).
- Late acks (`acks_late=True`, prefetch=1): a crashed worker loses no task; the idempotent
  claim step makes re-delivery safe.

## Quickstart

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py migrate
```

The API is at http://localhost:8000/api/v1/ , interactive docs at
http://localhost:8000/api/docs/ . The first processed document downloads the embedding
model (~220 MB) into the `model_cache` volume; later runs load it from cache.

## API endpoints

| Method | Path                             | Auth   | Description                                            |
| ------ | -------------------------------- | ------ | ------------------------------------------------------ |
| POST   | `/api/v1/auth/register/`         | public | Register (username, email, password)                   |
| POST   | `/api/v1/auth/login/`            | public | Obtain access/refresh JWT pair                         |
| POST   | `/api/v1/auth/refresh/`          | public | Refresh access token (rotates refresh token)           |
| GET    | `/api/v1/auth/me/`               | JWT    | Current authenticated user                             |
| POST   | `/api/v1/documents/`             | JWT    | Upload a document (multipart `file`, optional `title`) |
| GET    | `/api/v1/documents/`             | JWT    | List my documents (paginated, page size 20)            |
| GET    | `/api/v1/documents/{id}/`        | JWT    | Retrieve one of my documents (incl. `chunk_count`)     |
| DELETE | `/api/v1/documents/{id}/`        | JWT    | Delete a document (DB row + stored file)               |
| POST   | `/api/v1/documents/{id}/reprocess/` | JWT | Re-queue processing of a **failed** document (409 otherwise) |

Document list supports `?status=pending|processing|ready|failed`, `?search=<title>`,
`?ordering=created_at|title|size_bytes` (prefix `-` for descending).

Upload rules: only `.pdf` and `.txt`, max 10 MB (`MAX_UPLOAD_SIZE`), PDFs must start
with the `%PDF-` magic bytes, TXT must be valid UTF-8. Files are stored under
`media/documents/<user_id>/`; the storage path is never exposed and there is no
download endpoint.

## Environment variables

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `REDIS_URL` | `redis://redis:6379/0` | cache + Celery broker/results |
| `CELERY_TASK_ALWAYS_EAGER` | `False` | run tasks synchronously (tests) |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | chunking characters |
| `EMBEDDING_PROVIDER` | `local` | `local` (fastembed) \| `fake` (deterministic, offline) \| `openai` (HTTP) |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | fastembed model |
| `EMBEDDING_DIMENSIONS` | `384` | must match the model |
| `EMBEDDING_BATCH_SIZE` | `32` | texts per embed call |
| `EMBEDDING_CACHE_DIR` | `/home/appuser/.cache/fastembed` | mounted model cache volume |
| `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY` / `EMBEDDING_API_MODEL` | — | only for `openai` provider |

## Tests and linting

```bash
docker compose exec web pytest        # tests + coverage (fails under 85%)
docker compose exec web ruff check .
docker compose exec web ruff format --check .
docker compose exec web python manage.py makemigrations --check
```

Tests run with `EMBEDDING_PROVIDER=fake` and eager Celery (see `tests/conftest.py`),
so they need no model download, broker or worker.

## Project layout

```
config/              project, split settings (base/dev/prod), urls, celery app
apps/users/          custom User, JWT auth + registration (serializers -> services)
apps/documents/      Document + Chunk models, validators, extraction, chunking,
                     processing services, tasks, API
apps/common/         shared errors, embedding provider abstraction (fake/local/openai)
apps/chat/           reserved for a later phase
tests/               factories, fixtures, pytest suite, minimal PDF builder
```

Business logic lives in services and dedicated modules; Celery tasks are thin wrappers.
