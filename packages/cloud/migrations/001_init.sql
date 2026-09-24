-- CamAI cloud schema (Postgres + TimescaleDB).
-- Apply in order:  psql "$CAMAI_PG_DSN" -f 001_init.sql
--
-- Events are a Timescale hypertable (time-partitioned) because the workload is
-- append-only time-series counts. Aggregates live in 002_continuous_aggregates.sql.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   text PRIMARY KEY,
    name        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing_accounts (
    tenant_id                   text PRIMARY KEY,
    stripe_customer_id          text,
    stripe_subscription_item_id text,
    plan                        text
);

CREATE TABLE IF NOT EXISTS heartbeats (
    device_id  text PRIMARY KEY,
    tenant_id  text NOT NULL,
    ts         timestamptz NOT NULL,
    payload    jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    time          timestamptz NOT NULL,
    event_id      uuid        NOT NULL,
    tenant_id     text        NOT NULL,
    site_id       text        NOT NULL,
    camera_id     text        NOT NULL,
    type          text        NOT NULL,
    mode          text        NOT NULL,
    zone_id       text,
    line_id       text,
    object_class  text,
    count         integer,
    delta         integer,
    dwell_seconds double precision,
    clip_ref      text,
    labels        jsonb            -- small event tags, e.g. missing PPE items
);

SELECT create_hypertable('events', 'time', if_not_exists => TRUE);

-- Idempotency: a retried batch re-sends events with the same id AND the same ts,
-- so this unique index makes ingest ON CONFLICT DO NOTHING dedupe correctly.
-- (A hypertable's unique index must include the partitioning column, time.)
CREATE UNIQUE INDEX IF NOT EXISTS events_event_id_time_uidx ON events (event_id, time);

CREATE INDEX IF NOT EXISTS events_tenant_time_idx      ON events (tenant_id, time DESC);
CREATE INDEX IF NOT EXISTS events_tenant_cam_time_idx  ON events (tenant_id, camera_id, time DESC);
CREATE INDEX IF NOT EXISTS events_tenant_type_time_idx ON events (tenant_id, type, time DESC);

-- ---------------------------------------------------------------------------
-- Row-level security: every read/write must be scoped to a tenant. The app sets
-- `app.tenant_id` per connection/transaction; an unset GUC matches no rows, so a
-- missing scope fails closed rather than leaking cross-tenant data.
-- Run the API under a NON-owner role so these policies are enforced (table owners
-- bypass RLS unless FORCE ROW LEVEL SECURITY is set).
-- ---------------------------------------------------------------------------
ALTER TABLE events     ENABLE ROW LEVEL SECURITY;
ALTER TABLE heartbeats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS events_tenant_isolation ON events;
CREATE POLICY events_tenant_isolation ON events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS heartbeats_tenant_isolation ON heartbeats;
CREATE POLICY heartbeats_tenant_isolation ON heartbeats
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
