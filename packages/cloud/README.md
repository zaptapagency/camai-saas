# CamAI Cloud (control plane — ingest slice)

The multi-tenant cloud that every edge agent syncs to. This is the **first slice**:
a working, idempotent, tenant-guarded ingest endpoint plus a thin read API for the
dashboard. Auth (mTLS), TimescaleDB, billing, and fleet management build on top.

## Run locally

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ../schema          # shared event contract
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- Health: `GET http://localhost:8000/health`
- OpenAPI docs: `http://localhost:8000/docs`

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/ingest/events` | Edge agents POST an `EventBatch`. Idempotent by `event_id`; returns `IngestAck`. |
| POST | `/v1/ingest/heartbeat` | Device/stream health. |
| GET | `/v1/tenants/{id}/events` | Recent events (dashboard read side). |
| GET | `/v1/tenants/{id}/devices` | Latest heartbeat per device. |
| GET | `/v1/tenants/{id}/summary` | Aggregated dashboard tiles (incl. active cameras). |
| GET | `/v1/tenants/{id}/usage` | Customer usage view: active cameras this period + plan. |

## Point the edge agent here

In the edge agent's site config set:

```yaml
cloud:
  ingest_url: "http://localhost:8000"
```

## Production notes (see docs/ROADMAP.md)

- Swap `app/store.py` (SQLite) for a Postgres + TimescaleDB repository. The API
  doesn't change.
- Replace the `X-Device-Tenant` header with real mTLS: derive `tenant_id` from the
  client certificate. The `_resolve_tenant` seam in `app/main.py` is where this
  plugs in — the body `tenant_id` becomes a cross-check only.
- Enforce Postgres row-level security so a tenant can never read another's rows.

## TimescaleDB (production backend)

The API runs on SQLite by default. To use TimescaleDB, apply the migrations in
[`migrations/`](migrations/README.md) then set:

```bash
export CAMAI_STORE=postgres
export CAMAI_PG_DSN="postgresql://camai_app:***@db-host:5432/camai"
```

`store_factory.py` selects the backend; `PgStore` implements the same interface as
the SQLite store, so nothing else changes.

## Billing (Stripe metered usage)

Billing unit: **active camera per month** (a camera that reported ≥1 event).

1. Create the Stripe product/price and, per tenant, store the customer +
   subscription-item ids: `store.upsert_billing_account(tenant, cus_id, si_id, plan)`.
2. Schedule the nightly job:

```bash
# dry run — count only, no Stripe calls
python -m app.billing_job --dry-run

# real run
STRIPE_API_KEY=sk_live_... python -m app.billing_job
```

Usage is reported with `action="set"`, so re-running the job never double-counts.
Customers see their live count at `GET /v1/tenants/{id}/usage`.

## Tests

```bash
$env:PYTHONPATH = "../schema;."
pytest tests -q
```
