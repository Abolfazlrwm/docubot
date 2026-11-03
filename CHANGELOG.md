# Changelog

All notable changes to DocuBot are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-11-02

### Fixed

- **`POST /conversations/` returned HTTP 500** — the serializer never set the
  owner, so every create violated the not-null constraint. The view now sets
  `owner` from the authenticated request (`perform_create`).
- `QUERY_MAX_LENGTH` and `RETRIEVAL_MAX_TOP_K` were bound at import time, so
  runtime overrides (ops tuning, tests) were ignored. Both are now validated
  against live settings.
- The fake embedding provider hashed whole strings, making textually related
  documents totally dissimilar — retrieval demos and semantic tests were
  meaningless without a real model. It now embeds hashed **bag-of-words**
  vectors: identical texts match, overlapping texts rank high, unrelated texts
  score ~0. Still deterministic and offline.
- Demo UI: the document-library overlay covered the whole app because
  `display: flex` overrode the `hidden` attribute.
- Project-level static files (`/static/demo/…`) were never discovered —
  added `STATICFILES_DIRS`.

### Added

- **Streaming answers (SSE)**: `POST /api/v1/conversations/{id}/messages/stream/`
  emits `sources` → `delta` → `done` (or `error`) events, persists the turn,
  and keeps partial answers on client disconnect. All LLM providers gained
  `generate_stream()` (real SSE parsing for OpenAI-compatible and Anthropic,
  graceful chunked fallback otherwise).
- **Rate limiting**: scoped DRF throttles on `auth` (login/register/refresh),
  `chat`, `search` and `upload`; rates tunable via `THROTTLE_*` env vars.
  Reads are never metered.
- **Health endpoint**: `GET /api/v1/health/` reports database, cache and Celery
  status; fatal for DB/cache (503), non-fatal for Celery (`degraded`). Used by
  the Docker `HEALTHCHECK`, the worker healthcheck and the demo UI status dot.
- **Demo UI** at `/demo/`: register/login, document library with drag & drop
  upload and live status polling, streaming chat with numbered citations,
  source chips and token usage. Zero build step, vanilla JS.
- GitHub project kit: CI workflow (ruff + migrations + pytest on live
  pgvector/Redis services), `LICENSE` (MIT), `CONTRIBUTING.md`,
  `SECURITY.md`, issue/PR templates, Dependabot, `Makefile`,
  `docker-compose.prod.yml` (gunicorn + collectstatic + healthchecks),
  `docs/ARCHITECTURE.md`, this changelog, and a rewritten README.
- Production static serving via WhiteNoise.

### Changed

- Project name unified to **DocuBot** (API title, README); package layout unchanged.
- Tests: LLM provider stubs now patch where the function is used
  (`apps.chat.services`), N+1 guards reflect the real query count, and test
  fixtures use retrievable sample data. Suite size grew from 212 to 243 tests.

## [1.0.0] — 2026-10 (initial project)

- JWT auth (registration, login, refresh with rotation + blacklist, `/me`).
- Document upload (PDF/TXT) with content-based validation and async processing
  pipeline (extract → chunk → embed) on Celery with idempotent claims, stale
  claim recovery and retry/backoff.
- pgvector storage with HNSW index and filtered-scan tuning.
- Semantic search endpoint and RAG chat over conversations with provider
  abstractions (fastembed / OpenAI-compatible / Anthropic / offline fakes).
- OpenAPI 3 schema + Swagger UI via drf-spectacular; 212 tests with an 85%
  coverage gate.
