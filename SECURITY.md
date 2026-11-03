# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| latest `main` | ✅ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Open a [private security advisory](../../security/advisories/new) on GitHub, or
reach out to the maintainers directly. Include a description, reproduction
steps, and the affected component (API, worker, demo UI, docker setup).

You will receive an acknowledgement within a few days and a fix timeline once
the issue is triaged.

## Security posture & design notes

DocuBot is a multi-tenant API, and isolation is designed in rather than bolted on:

- **Tenancy is enforced at the query layer.** Every document/chunk/conversation
  queryset is filtered by the authenticated owner; foreign ids 404 exactly like
  nonexistent ones, so ids cannot be enumerated.
- **Retrieval cannot leak across owners.** Vector search runs a single SQL query
  whose `WHERE document.owner = request.user` clause is part of the scan, not an
  afterthought. Injected `document_ids` can only ever narrow the caller's own set.
- **JWT rotation + blacklist**: refresh tokens rotate on every use; used refresh
  tokens are blacklisted.
- **Uploads are validated by content**, not by client-supplied metadata: allowed
  extensions, size cap, `%PDF-` magic bytes, UTF-8 checks. Storage paths are
  randomised per owner and there is no download endpoint by default.
- **Prompt injection is mitigated in the system prompt** (document text is
  untrusted data, never instructions) and answers must cite their sources.
- **Rate limits** protect auth endpoints against brute force and chat/search
  against abuse (see `THROTTLE_*` env vars).
- The LLM/embedding provider abstractions never log API keys, prompts or
  document contents; error messages carry lengths and ids only.

## Known limitations

- The demo UI stores JWTs in `localStorage` for simplicity; production UIs
  should prefer in-memory tokens with silent refresh.
- File storage is local disk (`media/`); switch `DEFAULT_FILE_STORAGE` to an
  object store (S3) for multi-node deployments.
- No per-request content scanning beyond the validators listed above.
