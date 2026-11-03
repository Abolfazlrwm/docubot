# Contributing to DocuBot

Thanks for your interest in improving DocuBot! This document explains how to get
a development environment running and the conventions the codebase follows.

## Development setup

```bash
git clone <your-fork-url> && cd docubot
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py migrate
```

The API is then at http://localhost:8000/api/v1/ and the interactive docs at
http://localhost:8000/api/docs/. The demo UI lives at http://localhost:8000/demo/.

### Running the checks locally

Every pull request runs this exact sequence in CI — please run it before pushing:

```bash
docker compose exec web ruff check .
docker compose exec web ruff format --check .
docker compose exec web python manage.py makemigrations --check
docker compose exec web pytest          # coverage must stay >= 85%
```

## How the codebase is organised

- **Business logic lives in `services.py` modules and dedicated helpers**
  (`chunking.py`, `extraction.py`, `retrieval.py`); Celery tasks and DRF views
  are thin wrappers. If you add behaviour, put it in a service so it stays
  testable without HTTP.
- **Every user-facing change ships with tests.** The suite runs against a real
  Postgres (pgvector) service; embedding/LLM calls use the deterministic fake
  providers, so tests never need network access or API keys.
- **Errors have a contract.** Document processing distinguishes
  `PermanentProcessingError` (never retry) from `TransientProcessingError`
  (retry with backoff). LLM providers follow `LLMTransientError` /
  `LLMPermanentError`. Map new failure modes into this taxonomy.

## Style

- `ruff format` decides formatting — do not hand-format.
- Line length is 100 characters.
- Comments explain *why*, not *what*. Docstrings explain the contract.

## Submitting changes

1. Fork the repository and create a topic branch (`feat/streaming-filters`).
2. Make your change with focused commits.
3. Add or update tests; keep coverage at or above the gate.
4. Update `README.md` / `docs/ARCHITECTURE.md` when behaviour changes.
5. Open a pull request describing *what* and *why*. CI must be green.

## Reporting bugs

Please use the bug report issue template and include the docker compose logs of
`web` and `worker` for backend issues, or the browser console for demo UI issues.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
