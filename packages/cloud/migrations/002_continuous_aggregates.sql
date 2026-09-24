-- Continuous aggregates + retention.  psql "$CAMAI_PG_DSN" -f 002_continuous_aggregates.sql
--
-- These pre-roll the raw event stream so dashboard queries stay cheap at scale:
-- the dashboard reads the aggregates, not the raw hypertable. This is the payoff
-- of Timescale over plain Postgres for this workload.

-- Hourly rollup per camera/zone/line/type: counts, and gauge stats for occupancy.
CREATE MATERIALIZED VIEW IF NOT EXISTS events_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    tenant_id, site_id, camera_id, type, zone_id, line_id,
    count(*)     AS event_count,   -- e.g. entries/exits per hour
    sum(delta)   AS sum_delta,     -- warehouse net change
    avg(count)   AS avg_count,     -- occupancy_sample gauge, hourly mean
    max(count)   AS max_count      -- occupancy_sample gauge, hourly peak
FROM events
GROUP BY bucket, tenant_id, site_id, camera_id, type, zone_id, line_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('events_hourly',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Daily active-camera roster per tenant — the source for metered billing and the
-- customer usage view (a camera is "active" for a day if it emitted any event).
CREATE MATERIALIZED VIEW IF NOT EXISTS active_cameras_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS day,
    tenant_id, camera_id,
    count(*) AS events
FROM events
GROUP BY day, tenant_id, camera_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('active_cameras_daily',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- Retention (from the plan): keep raw events ~90 days; the aggregates persist and
-- carry the long-term trends and billing history.
SELECT add_retention_policy('events', INTERVAL '90 days', if_not_exists => TRUE);
