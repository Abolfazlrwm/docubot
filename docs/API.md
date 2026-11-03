# API Reference

All endpoints live under `/api/v1/`. Interactive docs: `/api/docs/` (Swagger UI)
and `/api/schema/` (raw OpenAPI 3).

Unless stated otherwise, endpoints require a JWT access token:

```
Authorization: Bearer <access token>
```

Every list endpoint is paginated (`?page=` / `?page_size=`) and every queryset is
scoped to the authenticated user — other users' objects 404 like nonexistent ones.

## Authentication

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v1/auth/register/` | Create an account |
| POST | `/api/v1/auth/login/` | Obtain access + refresh tokens |
| POST | `/api/v1/auth/refresh/` | Rotate the refresh token, get a new access token |
| GET  | `/api/v1/auth/me/` | Current user profile |

```bash
# Register
curl -X POST http://localhost:8000/api/v1/auth/register/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "ada", "email": "ada@example.com", "password": "a-strong-passw0rd"}'

# Log in -> {"access": "...", "refresh": "..."}
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "ada", "password": "a-strong-passw0rd"}'

# Refresh (old refresh token is blacklisted — rotation is enforced)
curl -X POST http://localhost:8000/api/v1/auth/refresh/ \
  -H 'Content-Type: application/json' -d '{"refresh": "<refresh token>"}'
```

## Documents

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/documents/` | List my documents (`?status=`, `?search=`, `?ordering=`) |
| POST | `/api/v1/documents/` | Upload PDF/TXT (`multipart/form-data`), returns **202** |
| GET | `/api/v1/documents/{id}/` | Inspect one document |
| DELETE | `/api/v1/documents/{id}/` | Delete document, chunks and stored file |
| POST | `/api/v1/documents/{id}/reprocess/` | Re-queue a **failed** document (409 otherwise) |

```bash
curl -X POST http://localhost:8000/api/v1/documents/ \
  -H "Authorization: Bearer $ACCESS" \
  -F "title=Product Handbook" \
  -F "file=@handbook.pdf"
```

Upload returns `202 Accepted` immediately; processing (extract → chunk → embed)
runs on Celery. Poll `status` until it leaves `pending`/`processing`:

| status | meaning |
| --- | --- |
| `pending` | queued, no worker picked it up yet |
| `processing` | extraction/embedding in progress |
| `ready` | chunks embedded and searchable |
| `failed` | see `error_message`; can be reprocessed |

## Search

```bash
curl -X POST http://localhost:8000/api/v1/search/ \
  -H "Authorization: Bearer $ACCESS" -H 'Content-Type: application/json' \
  -d '{"query": "how do I reset my password?", "top_k": 5}'
```

| Field | Type | Notes |
| --- | --- | --- |
| `query` | string | required, ≤ `QUERY_MAX_LENGTH` (validated live) |
| `top_k` | int | optional, 1…`RETRIEVAL_MAX_TOP_K` |
| `document_ids` | int[] | optional filter |

Response: ranked hits with `document_id`, `document_title`, `chunk_id`,
`score`, `text`. Only the caller's `ready` documents are ever searched.

## Chat

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/conversations/` | My conversations (newest activity first) |
| POST | `/api/v1/conversations/` | Create `{ "title": "..." }` |
| GET | `/api/v1/conversations/{id}/messages/` | Chronological history |
| POST | `/api/v1/conversations/{id}/messages/` | Ask a question, get the full answer as JSON |
| POST | `/api/v1/conversations/{id}/messages/stream/` | Same turn, streamed as **SSE** |

JSON turn request: `{ "content": "...", "document_ids": [1, 2] }` (content ≤ 2000
chars). Response `201`:

```json
{
  "id": 42,
  "role": "assistant",
  "content": "Reset it from Settings → Security [1].",
  "sources": [{"marker": 1, "document_id": 7, "document_title": "Handbook", "snippet": "…"}],
  "model": "gpt-4o-mini",
  "prompt_tokens": 412,
  "completion_tokens": 38,
  "created_at": "2026-11-02T10:00:00Z"
}
```

### Streaming (Server-Sent Events)

`POST /conversations/{id}/messages/stream/` responds with
`Content-Type: text/event-stream` and emits, in order:

```
event: sources        data: {"sources": [{"marker": 1, ...}]}   # once, first
event: delta          data: {"text": "Reset"}                   # repeated
event: delta          data: {"text": " it from…"}
event: done           data: {"message_id": 42, "content": "…", "sources": [...]}
```

* `done` means the turn is persisted (user + assistant messages).
* On LLM failure: `event: error data: {"detail": "…"}` — nothing is persisted.
* Client disconnect: the partial answer is still persisted (`GeneratorExit` path).

```js
const es = new EventSource(`/api/v1/conversations/${id}/messages/stream/`, {
  method: "POST", headers: {Authorization: `Bearer ${token}`}, body: formData,
});
es.addEventListener("delta", (e) => append(JSON.parse(e.data).text));
```

(For browsers that can't set headers on `EventSource`, the demo UI posts with
`fetch` and reads the stream manually — see `static/demo/app.js`.)

## Health

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/health/` | Liveness/readiness for orchestrators (no auth) |

`200` when all vital components are healthy:

```json
{"status": "ok", "django": "ok", "components": {"database": "ok", "cache": "ok", "celery": "ok"}}
```

* Database or cache failing → **503** with `status: "error"` (the service cannot work).
* Celery unreachable → still **200** with `components.celery: "degraded"` (uploads
  stall but the API is alive).
* Used by the Docker `HEALTHCHECK`, the worker healthcheck and the demo UI status dot.

## Rate limits

Writes are throttled per user (scoped buckets, only unsafe methods count):

| Scope | Endpoints | Default | Env override |
| --- | --- | --- | --- |
| `auth` | register / login / refresh | 30/min | `THROTTLE_AUTH` |
| `chat` | ask + stream | 60/min | `THROTTLE_CHAT` |
| `search` | semantic search | 60/min | `THROTTLE_SEARCH` |
| `upload` | document upload / reprocess | 60/hour | `THROTTLE_UPLOAD` |

Exceeding a bucket returns `429 Too Many Requests` with a `Retry-After` header.
Reads (GET/HEAD/OPTIONS) are never metered.
