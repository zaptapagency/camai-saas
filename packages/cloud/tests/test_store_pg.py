"""Integration tests for the Postgres-backed store (`PgStore`).

These run against a **real Postgres server** started via `pgserver` (a pip-bundled
Postgres that needs no admin/Docker). They exercise every runtime code path of
PgStore — inserts + idempotency, DISTINCT ON summary queries, active-camera
metering, billing CRUD — and verify row-level-security actually isolates tenants
when connecting as a non-owner role.

Scope note: the embedded server has no TimescaleDB extension, so this applies the
Timescale-free subset of the schema (tables, indexes, RLS). PgStore never calls
hypertable/continuous-aggregate functions itself — a hypertable behaves like an
ordinary table for INSERT/SELECT — so this covers 100% of PgStore's query paths.
Validating the Timescale DDL in `migrations/` still requires a real TimescaleDB
(Docker `timescale/timescaledb`), tracked in docs/ROADMAP.md.

Skipped automatically if `pgserver`/`psycopg` aren't installed.
"""

import os
import tempfile

import pytest

psycopg = pytest.importorskip("psycopg")

from camai_schema import Event, EventType, Heartbeat, Mode
from app.store_pg import PgStore

# Timescale-free schema: same tables/indexes/RLS as migrations/001_init.sql,
# minus `CREATE EXTENSION timescaledb` and `create_hypertable`.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_accounts (
    tenant_id text PRIMARY KEY, stripe_customer_id text,
    stripe_subscription_item_id text, plan text);
CREATE TABLE IF NOT EXISTS heartbeats (
    device_id text PRIMARY KEY, tenant_id text NOT NULL,
    ts timestamptz NOT NULL, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    time timestamptz NOT NULL, event_id uuid NOT NULL, tenant_id text NOT NULL,
    site_id text NOT NULL, camera_id text NOT NULL, type text NOT NULL,
    mode text NOT NULL, zone_id text, line_id text, object_class text,
    count integer, delta integer, dwell_seconds double precision, clip_ref text,
    labels jsonb);
ALTER TABLE events ADD COLUMN IF NOT EXISTS labels jsonb;  -- for a pre-existing table
CREATE UNIQUE INDEX IF NOT EXISTS events_event_id_time_uidx ON events (event_id, time);
CREATE INDEX IF NOT EXISTS events_tenant_time_idx ON events (tenant_id, time DESC);
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE heartbeats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS events_tenant_isolation ON events;
CREATE POLICY events_tenant_isolation ON events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS heartbeats_tenant_isolation ON heartbeats;
CREATE POLICY heartbeats_tenant_isolation ON heartbeats
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
"""


@pytest.fixture(scope="module")
def dsn():
    # Prefer an externally provided server (CAMAI_PG_DSN); otherwise try the
    # pip-bundled `pgserver` (Linux/macOS only). Skip if neither is available.
    external = os.environ.get("CAMAI_PG_DSN")
    if external:
        with psycopg.connect(external, autocommit=True) as conn:
            conn.execute(_SCHEMA)
        yield external
        return

    pgserver = pytest.importorskip("pgserver", reason="no CAMAI_PG_DSN and pgserver unavailable")
    d = tempfile.mkdtemp(prefix="camai-pg-")
    server = pgserver.get_server(d)
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(_SCHEMA)
    try:
        yield uri
    finally:
        server.cleanup()


@pytest.fixture
def store(dsn):
    # clean slate per test
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE events, heartbeats, billing_accounts")
    return PgStore(dsn)


def _ev(tenant="t1", camera="c1", **kw):
    base = dict(tenant_id=tenant, site_id="s1", camera_id=camera)
    base.update(kw)
    return Event(**base)


def test_insert_and_idempotency(store):
    events = [_ev(type=EventType.entry, mode=Mode.retail, line_id="door"),
              _ev(type=EventType.occupancy_sample, mode=Mode.retail, count=3)]
    accepted, dups = store.insert_events(events)
    assert (accepted, dups) == (2, 0)
    # Re-sending the same events dedupes on (event_id, time).
    accepted2, dups2 = store.insert_events(events)
    assert (accepted2, dups2) == (0, 2)
    assert len(store.recent_events("t1")) == 2


def test_summary_matches_sqlite_semantics(store):
    store.insert_events([
        _ev(type=EventType.entry, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.entry, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.exit, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.occupancy_sample, mode=Mode.retail, count=5),
        _ev(camera="lot", type=EventType.vehicle_parked, mode=Mode.parking, zone_id="s1"),
    ])
    s = store.summary("t1")
    assert s["totals"]["entries"] == 2
    assert s["totals"]["exits"] == 1
    assert s["totals"]["retail_occupancy"] == 1
    assert s["totals"]["parking_spaces_occupied"] == 1
    assert any(o["count"] == 5 for o in s["occupancy"])
    assert any(p["state"] == "parked" for p in s["parking"])


def test_heartbeat_upsert(store):
    hb = Heartbeat(device_id="d1", tenant_id="t1", agent_version="0.1.0",
                   uptime_seconds=10, stream_fps={"c1": 8.0}, queued_events=0)
    store.upsert_heartbeat(hb)
    devices = store.device_health("t1")
    assert len(devices) == 1 and devices[0]["stream_fps"]["c1"] == 8.0


def test_active_camera_ids_windowed(store):
    from app.billing import month_bounds
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    start, end = month_bounds(now)
    store.insert_events([_ev(camera="a", type=EventType.occupancy_sample, mode=Mode.parking, count=1),
                         _ev(camera="b", type=EventType.occupancy_sample, mode=Mode.parking, count=1)])
    ids = store.active_camera_ids("t1", start.isoformat(), end.isoformat())
    assert ids == ["a", "b"]


def test_ppe_violation_labels_roundtrip(store):
    store.insert_events([
        _ev(camera="dock", type=EventType.ppe_violation, mode=Mode.safety,
            zone_id="hazard", track_id=1, count=1, labels=["vest"]),
    ])
    s = store.summary("t1")
    assert s["totals"]["ppe_violations"] == 1
    recent = store.recent_events("t1")
    assert any(e["labels"] == ["vest"] for e in recent)   # jsonb -> list


def test_billing_account_crud(store):
    assert store.get_billing_account("t1") is None
    store.upsert_billing_account("t1", "cus_1", "si_1", "growth")
    acct = store.get_billing_account("t1")
    assert acct["plan"] == "growth" and acct["stripe_subscription_item_id"] == "si_1"
    store.upsert_billing_account("t1", "cus_1", "si_2", "enterprise")
    assert store.get_billing_account("t1")["plan"] == "enterprise"
    assert len(store.all_billing_accounts()) == 1


def test_row_level_security_isolates_tenants(store, dsn):
    """A non-owner role scoped to tenant t1 must not see t2's rows."""
    store.insert_events([_ev(tenant="t1", type=EventType.entry, mode=Mode.retail),
                         _ev(tenant="t2", type=EventType.entry, mode=Mode.retail)])
    with psycopg.connect(dsn, autocommit=True) as admin:
        # Idempotent teardown across runs against a persistent DB: a role that
        # still holds grants cannot be dropped, so drop what it owns first.
        admin.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT FROM pg_roles WHERE rolname='camai_app') THEN "
            "EXECUTE 'DROP OWNED BY camai_app'; EXECUTE 'DROP ROLE camai_app'; "
            "END IF; END $$;"
        )
        admin.execute("CREATE ROLE camai_app LOGIN PASSWORD 'x'")
        admin.execute("GRANT SELECT ON events TO camai_app")

    # Connect as the non-owner role so RLS is enforced.
    import re
    app_dsn = re.sub(r"//[^@/]+@", "//camai_app:x@", dsn) if "@" in dsn else dsn + "?user=camai_app&password=x"
    with psycopg.connect(app_dsn, autocommit=True) as app:
        app.execute("SELECT set_config('app.tenant_id', 't1', false)")
        rows = app.execute("SELECT tenant_id FROM events").fetchall()
    assert rows and all(r[0] == "t1" for r in rows)  # only t1 visible
