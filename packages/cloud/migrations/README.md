# Cloud database migrations (Postgres + TimescaleDB)

The API runs on an embedded SQLite store by default (zero setup). For production,
point it at TimescaleDB.

## Apply

```bash
export CAMAI_PG_DSN="postgresql://camai_app:***@db-host:5432/camai"
psql "$CAMAI_PG_DSN" -f 001_init.sql
psql "$CAMAI_PG_DSN" -f 002_continuous_aggregates.sql
```

`001_init.sql` — tenants, billing accounts, heartbeats, and the `events`
hypertable (idempotent unique index on `(event_id, time)`, tenant indexes,
row-level security).
`002_continuous_aggregates.sql` — hourly rollups (`events_hourly`), the daily
active-camera roster (`active_cameras_daily`), refresh policies, and 90-day raw
retention.

## Point the API at Postgres

```bash
export CAMAI_STORE=postgres
export CAMAI_PG_DSN="postgresql://camai_app:***@db-host:5432/camai"
uvicorn app.main:app --port 8000
```

The API/dashboard/billing code is backend-agnostic (`app/store_factory.py`).

## Row-level security

Policies scope every row to `current_setting('app.tenant_id')`, which the store
sets per query. **Run the API under a non-owner role** (e.g. `camai_app`) — table
owners bypass RLS unless `FORCE ROW LEVEL SECURITY` is set. Grant that role
`SELECT/INSERT` on the tables and `USAGE` on the schema, but not ownership.

## Migration tooling

These are plain idempotent SQL files for the pilot. Adopt Alembic or `sqitch` once
schema changes need ordered, versioned, reversible deploys across environments.
