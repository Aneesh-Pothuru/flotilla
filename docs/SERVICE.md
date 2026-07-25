# Installed service

The hosted FLOTILLA site is a static, deterministic strategy simulator. The
installed service is a separate, real execution path over the same safe
predicate evaluator and paired-measurement executor. It persists portfolio
state, approvals, runs, scores, budget entries, decisions, reallocations, and
request lineage in SQLite.

## Start locally

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install .
flotilla serve --budget 12
```

The default endpoint is `http://127.0.0.1:8765`. The default bind is loopback,
so no API token is required. Binding to a non-loopback address fails closed
unless `FLOTILLA_API_TOKEN` contains at least 16 characters.

```bash
export FLOTILLA_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FLOTILLA_ALLOWED_ORIGIN="https://flotilla.apothuru.com"
flotilla serve --host 0.0.0.0 --budget 12
```

`FLOTILLA_ALLOWED_ORIGIN` accepts one exact origin. Wildcard CORS is rejected.
The browser token is kept only in memory and is never put in local storage.

## Register, approve, and execute

Initialize is idempotent when the budget matches:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/portfolio \
  -H 'Content-Type: application/json' \
  -d '{"total_budget":12}'
```

Register an executable paired-measurement thesis and plan:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/theses \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: onboarding-T-14' \
  -d '{
    "id":"T-14",
    "title":"A registered intervention improves the paired score",
    "prediction":"The treatment improves the registered score by at least 0.02.",
    "kill_predicate":"ci_lower < 0.02",
    "budget_cap":4,
    "decision_deadline":"2026-08-01T17:00:00Z",
    "limitations":["Three paired observations in this local example."],
    "plan":{"version":1,"nodes":[
      {"id":"falsifier","kind":"falsifier","cost":1,
       "params":{"seed":41,"control":[0.70,0.71,0.69],
                 "treatment":[0.75,0.76,0.74]}},
      {"id":"replicate","kind":"replicate","cost":2,
       "depends_on":["falsifier"],
       "params":{"seed":42,"control":[0.69,0.70,0.71],
                 "treatment":[0.75,0.76,0.77]}}
    ]}
  }'

curl -sS -X POST http://127.0.0.1:8765/v1/theses/T-14/approve \
  -H 'Content-Type: application/json' \
  -d '{"actor":"research-lead"}'

curl -sS -X POST http://127.0.0.1:8765/v1/portfolio/step \
  -H 'Content-Type: application/json' -d '{}'
```

`step` always chooses unresolved falsifiers before follow-ups, then sorts by
cost and stable thesis/node identity. `run` repeats this operation:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/portfolio/run \
  -H 'Content-Type: application/json' -d '{"max_steps":100}'
```

No kill is automatically confirmed. A fired predicate produces
`PENDING_KILL`; missing evidence produces `UNDETERMINED`. A human must make an
explicit next decision:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/theses/T-14/confirm-kill \
  -H 'Content-Type: application/json' -d '{"actor":"independent-reviewer"}'

curl -sS -X POST http://127.0.0.1:8765/v1/theses/T-14/overturn \
  -H 'Content-Type: application/json' \
  -d '{"actor":"independent-reviewer","reason":"run the registered replicate"}'

curl -sS -X POST http://127.0.0.1:8765/v1/theses/T-14/revive \
  -H 'Content-Type: application/json' \
  -d '{"actor":"research-lead","reason":"new registered evidence"}'
```

Overturn requires `PENDING_KILL`; revive requires `KILLED`. Both append a
decision without deleting the evidence they challenge.

## Reallocate and reverse

Reallocation transfers unspent thesis cap, not already-spent portfolio budget.
It requires an actor and reason. The source cap can never fall below its spend,
and the target cap can never exceed the portfolio mandate.

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/reallocations \
  -H 'Content-Type: application/json' \
  -d '{"source_thesis_id":"T-01","target_thesis_id":"T-14","amount":1,
       "actor":"research-lead","reason":"move unused cap after review"}'

curl -sS -X POST http://127.0.0.1:8765/v1/reallocations/1/reverse \
  -H 'Content-Type: application/json' \
  -d '{"actor":"research-lead","reason":"restore the prior caps"}'
```

## Read and operate

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/healthz` | Process liveness; no auth |
| `GET` | `/readyz` | Database and portfolio readiness; no auth |
| `GET` | `/v1/portfolio` | Budget, statuses, thesis summaries |
| `GET` | `/v1/theses` | Thesis list |
| `GET` | `/v1/theses/{id}` | Thesis, plan, runs, decisions |
| `GET` | `/v1/runs/{id}` | One immutable run result |
| `GET` | `/v1/events?after=0&limit=100` | Ordered lineage page |
| `POST` | `/v1/theses/{id}/runs` | Execute one named approved node |
| `POST` | `/v1/portfolio/step` | Execute the next scheduled node |
| `POST` | `/v1/portfolio/run` | Execute until idle or the step limit |

Every response contains `X-Request-ID`. A valid incoming `X-Request-ID` is
retained; otherwise the server generates one. Mutations copy it into lineage.
Expected errors use:

```json
{"error":{"code":"PLAN_NOT_APPROVED","message":"unapproved plans cannot execute","request_id":"..."}}
```

Requests default to a 64 KiB maximum, numeric budgets must be finite and
positive, plans are bounded, DAG cycles are rejected, dependencies are
enforced, and all state-changing operations use an immediate SQLite
transaction.

## Docker Compose

```bash
export FLOTILLA_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose up --build
```

Compose publishes only `127.0.0.1:8765`, persists `/var/lib/flotilla` in a
named volume, drops Linux capabilities, sets `no-new-privileges`, and runs the
container filesystem read-only except for the data volume and a small tmpfs.

This is a single-node service. SQLite WAL plus a write lock makes local
concurrent requests coherent; it is not a distributed transaction system.
