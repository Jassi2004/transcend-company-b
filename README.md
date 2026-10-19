# Company B — a calmer support desk

A second, independently deployed fictional SaaS. **Python 3.12, FastAPI, SQLite, and browser-native JavaScript**: no shared application stack with Company A.

## Run

```sh
uv sync --locked --python 3.12
uv run uvicorn app:app --host 127.0.0.1 --port 4300
# http://localhost:4300
```

Alternatively: `pip install -r requirements.txt` then run uvicorn. Set `DATABASE_PATH` for a durable database volume. No real emails are sent. This fictional demo has no customer authentication; use synthetic data only.

## Working features

- Create support tickets with severity, server-assigned priority, and a stored response target.
- Resolve conversations, search/filter the queue, inspect priority counts and actual event history.
- Idempotency, transactional writes, validation, persistence, health, and classification APIs.
- Optional authenticated priority-inversion incident; approved recovery restores critical routing and reclassifies open tickets without modifying customer content.

## Verify

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

The tests exercise routing, retries, resolution, fault authentication, precondition conflicts, validation, origins, and persistence across app restarts.

GitHub Actions verifies the app, then publishes a commit-tagged Docker image to GHCR. The MAYA repository has the multi-service Render blueprint, which waits for passing CI before later auto-deploys.

## MAYA connection

Set `ENABLE_DEMO_FAULTS=true` and `SANDBOX_TOKEN` server-side. `POST /api/demo/fault` accepts `{"kind":"priority-inversion"}` or `{"kind":"none","expectedFault":"priority-inversion"}`, with a Bearer connector token. With the fault active, `/api/classify?severity=critical` returns `low`, and real new critical tickets are misrouted. Resetting the fault repairs routing and existing open demo tickets. Health reports degradation while the injected fault is active.

This is a controlled configuration-recovery demonstration, not a claim of autonomous source-code repair. MIT licensed.
