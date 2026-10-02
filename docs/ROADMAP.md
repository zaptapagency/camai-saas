# CamAI — Build Roadmap & Status

Maps the [plan](PLAN.md) to concrete engineering work. This tracks *what's built in
this repo*, not the go-to-market timeline.

## ✅ Done (foundation)

- **Shared event contract** (`packages/schema`) — `Event`, `EventBatch`,
  `Heartbeat`, `IngestAck`; idempotent (`event_id`), append-only, no raw video.
- **Edge vision pipeline** (`packages/edge-agent`) — RTSP/file/webcam ingest,
  YOLOv11 + ByteTrack detection/tracking, three counting modes on one pipeline
  (retail line-crossing, parking per-space occupancy, warehouse zone count),
  durable SQLite event queue, outbound-only batched cloud sync + heartbeats.
  Unit-tested counting logic (runs without the ML stack). **Verified end to end
  against live public cameras** (see "Testing against a public camera" in the
  root README): real vehicle/person detection, tracking with persistent IDs,
  occupancy + parked/left events.
- **Calibration wizard** (`packages/edge-agent` → `camai_edge.calibrate`) — local
  web tool: grabs a live snapshot per camera, draw entry/exit lines and
  zones/spaces on it, writes normalized geometry back into the site YAML. Run with
  `camai-calibrate --config <site.yaml>`. Write-back is unit-tested and the full
  browser→API→config loop is verified.
- **Cloud ingest slice + dashboard** (`packages/cloud`) — FastAPI ingest endpoint
  (idempotent, tenant-guarded), heartbeat endpoint, read API + `summary`
  aggregation, and a **served single-page dashboard** at `/` (active-cameras tile,
  live occupancy tiles, per-zone occupancy chart, parking states, device health,
  event feed). Ingest + summary tested; live data confirmed flowing from a real
  camera to the dashboard.
- **TimescaleDB backend** (`packages/cloud/migrations`, `app/store_pg.py`) — real
  schema: `events` hypertable (idempotent unique index), tenant indexes,
  row-level security; continuous aggregates (`events_hourly`,
  `active_cameras_daily`) + refresh/retention policies. `PgStore` implements the
  same interface as the SQLite dev store, selected by `CAMAI_STORE` via
  `store_factory.py`, so the API is backend-agnostic.
- **Stripe metered billing** (`app/billing.py`, `app/billing_job.py`) — active
  camera = one that reported ≥1 event in the period (the billed unit). Nightly job
  counts active cameras per tenant and reports to Stripe with `action="set"`
  (idempotent, no double-count); `GET /v1/tenants/{id}/usage` is the customer usage
  view. Now also a `MeterEventReporter` (Stripe Billing Meter Events API),
  `billing_setup.py` (idempotent product/price/subscription catalog), and an admin
  API `PUT/GET /v1/admin/tenants/{id}/billing`. Fully unit-tested with a mock client.
- **Postgres path integration-tested** — `PgStore` runs green against a real
  PostgreSQL server (`tests/test_store_pg.py`): inserts + idempotency, summary
  queries, active-camera metering, billing CRUD, and RLS tenant isolation via a
  non-owner role.
- **mTLS device identity** (`edge: identity.py`; `cloud: security.py`,
  `device_registry.py`) — per-device EC key + cert minted on first boot;
  `device_id` = public-key fingerprint (unforgeable); `current_device()` derives
  tenant/device from a proxy-verified client cert (`CAMAI_REQUIRE_MTLS` hardens
  prod); zero-touch `POST /v1/devices/register` with single-use enrollment tokens.
- **Fleet management** (`cloud: fleet.py`, `fleet_router.py`; `edge: fleet.py`) —
  desired-config push (auto-versioned), release pinning + deterministic canary
  rollout, and read-derived alerts (stale devices / degraded streams) at
  `GET /v1/tenants/{id}/alerts`; edge `FleetClient` pulls outbound-only.
- **ONVIF auto-discovery** (`edge: discovery.py`) — WS-Discovery LAN scan (stdlib)
  → camera candidates with RTSP URLs (+ manual fallback); wired into the
  calibration wizard as `GET /api/discover` and a "Discover cameras" dialog.
- **Accuracy harness** (`edge: accuracy.py`, `docs/accuracy.md`) — run clips (or a
  deterministic detections fixture) through the real counters and report ±% error
  vs. ground truth, with tolerance asserts — the basis for publishable accuracy.
- **Next.js dashboard** (`packages/dashboard`) — Next 14 + TS + Tailwind app
  (overview / devices / usage pages, Recharts, TanStack Query, stub RBAC) reading
  the cloud API; productionizes the served SPA.

- **Edge runtime fully wired** (`edge/main.py`) — on startup the agent calls
  `ensure_identity()` (mints an EC key + self-signed cert on first boot; falls back
  to the config `device_id` if `cryptography` is absent), uses the fingerprint
  `device_id` for all events/heartbeats, feeds the cert/key into `CloudConfig`,
  does best-effort zero-touch registration with `--enroll-token`, and runs the
  `FleetClient` poller when `fleet_enabled`. **Verified live**: a run against the
  Jackson Hole cam registered `dev_d88093…` (cert CN matches), heartbeats carried
  that id, and a pushed desired-config was served back to the poller.

- **Queue-length vertical** (`edge/counting/queue.py`, `mode: queue`) — a fourth
  counting mode on the same pipeline: instantaneous queue length as
  `occupancy_sample`, and per-person wait time as a `dwell` event on leave, with a
  grace period so occlusion in a packed line doesn't reset a wait. Dashboard shows
  an **avg-wait tile**. Unit-tested.
- **PPE/safety vertical** (`edge/counting/safety.py`, `mode: safety`) — a fifth mode:
  associates helmet/vest detections to each person in a required-PPE zone and raises
  a `ppe_violation` (debounced, carrying the missing items in the new `Event.labels`)
  plus a zone-headcount sample; dashboard shows a **PPE-violations tile**. A detector
  `class_map` hook (`DetectorConfig.classes`) lets a PPE-trained model plug into the
  same pipeline (COCO can't detect PPE). Unit-tested; `labels` persists on both
  stores (Postgres via a new `jsonb` column, covered by the PG integration test).
- **Smart-city traffic vertical** (`edge/counting/traffic.py`, `mode: traffic`) — a
  sixth mode: directional vehicle line-crossing emitting `vehicle_crossing` events
  with the direction in `labels` (forward/reverse). Reuses the existing COCO
  detector (no new model); an optional `detector.classes` map breaks volume down by
  vehicle type (car/truck/bus…). Dashboard shows a **vehicle-crossings tile**.
  Unit-tested.
- **Staffing / workstation-coverage vertical** (`edge/counting/staffing.py`,
  `mode: staffing`) — a seventh mode for e.g. restaurants: per-station staff
  headcount with a debounced staffed⇄unstaffed transition (grace period absorbs
  brief absences), **plus station activity** — a staffed station is tagged
  `active` while a present worker is moving (bounding-box motion — plating,
  reaching, turning) and flips to `static` once every present worker has been
  still past an idle grace, so "manned but standing idle during the rush" alerts
  as timely as "unmanned". **Anonymous and station-level** by design — activity is
  per *station*, never per employee: no biometric identification, no individual
  productivity scoring. Summary exposes `stations_total` / `stations_unstaffed` /
  `stations_active` / `stations_static`; dashboard shows **stations-unstaffed** and
  **stations-idle** tiles. Unit-tested.

- **Four more verticals on the same pipeline** (`edge/counting/{capacity,proximity,fire,thermal}.py`) —
  one per go-to-market tier, wired into `make_counter` and both cloud stores' summary:
  - **`capacity`** (Tier 1, no new model) — live headcount vs a zone's occupancy limit
    (`Zone.capacity`); emits a debounced `capacity_breach` (grace-windowed) plus over-tagged
    `occupancy_sample`s. Buyers: gyms, venues, clinics.
  - **`proximity`** (Tier 2, adds a forklift class) — forklift↔pedestrian near-miss: a
    `proximity_alert` when a person and forklift close within `proximity_threshold` (fraction of
    the frame diagonal), debounced per person with a clear-grace. Buyers: warehouses (OSHA).
  - **`fire`** (Tier 3, dedicated model) — fire/smoke `hazard_alert` (type set in `labels`,
    re-fires when the set changes, clear-grace re-arm), per-zone or whole-scene. Buyers: kitchens,
    warehouses.
  - **`thermal`** (Tier 4, extra sensor) — overheat/fever `overheat_alert` off a thermal camera's
    per-detection `Detection.temperature` vs `temp_threshold_c`, debounced per track. Buyers:
    access control, industrial.
  All four are anonymous/zone- or station-level, fully unit-tested, and surfaced as dashboard tiles
  (`capacity_breaches` / `proximity_alerts` / `hazard_alerts` / `overheat_alerts`).

- **Accuracy report across all verticals** (`edge/accuracy.py`, `accuracy_report.py`) —
  `tally_metrics` now reduces every event type (incl. ppe/traffic/dwell + the four new
  alerts); `python -m camai_edge.accuracy_report` runs a deterministic fixture suite over
  all 11 modes and writes `docs/accuracy_report.{md,json}` — the publishable-format
  infrastructure (real-camera numbers come from design-partner footage).
- **Zero-touch onboarding** (`edge/autozone.py`) — suggests calibration zones (grid-density
  clustering of foot points) and an entry line (dominant travel axis) from observed
  detections, so setup becomes review-and-confirm instead of draw-from-scratch. Unit-tested;
  next step is wiring it into the calibration wizard as proposed geometry.
- **Enterprise trust: RBAC + audit log** (`cloud: rbac.py`, `audit.py`) — a role seam
  (`viewer<analyst<admin<owner` via `X-CamAI-Role`, the pre-SSO seam a gateway derives from a
  verified OIDC session) gates the billing-admin and fleet config-push mutations; an
  append-only, tenant-scoped audit log records them and is read at
  `GET /v1/tenants/{id}/audit` (analyst+). Fully unit-tested.

**Test status: 200 passed, 1 skipped** across all packages incl. the Postgres integration
suite (the crypto-gated identity tests pass once `cryptography` is installed).

## 🔜 Next (remaining follow-ups)

1. **Validate the Timescale-specific DDL** — the integration test uses plain
   Postgres; stand up real TimescaleDB in CI to exercise the hypertable +
   continuous aggregates in `migrations/`.
2. **Flip ingest to cert identity** — switch `/v1/ingest/*` to `Depends(current_device)`
   behind a TLS-terminating proxy; set `CAMAI_REQUIRE_MTLS=1` in prod.
3. **Connect real external accounts** — a real Stripe account (keys, products) and
   real ONVIF cameras / recorded reference clips to publish accuracy numbers.
4. **Run/deploy the Next.js app** — `npm install` (Node not present in this env) and
   replace stub RBAC with real SSO/SAML.

## Known gaps / TODO in current code

- Detector weights (`yolo11n.pt`) auto-download on first run; pin + vendor
  fine-tuned per-site weights for production.
- Warehouse mode is beta — occlusion/stacking handling needs a tuning pass and
  possibly a second model.
- Ingest still uses the `X-Device-Tenant` seam by default; cert-derived identity
  (`app/security.py`) is built + tested but only enforced once ingest is switched
  to `Depends(current_device)` behind a TLS proxy (see Next #3).
- ~~Retail dwell-time events are modeled in the schema (`dwell`) but not yet emitted
  by the line-crossing counter~~ **Done** — the retail line-crossing counter now
  emits `dwell` events for any `zones` drawn on a retail camera (grace-windowed
  per-shopper presence, same mechanism as the queue counter). The cloud summary
  splits dwell **by mode**: queue dwell → `avg_wait_seconds` (avg wait), retail
  dwell → `avg_browse_seconds` (avg browse), so the two metrics don't pool. Both
  stores + the served SPA (Avg-wait and Avg-browse tiles) updated; unit-tested.
  Cameras with no zones are unchanged.
- No object storage / clip snapshot path yet (schema has `clip_ref`).

## How to run the tests

```bash
# from the repo root, with pydantic + pyyaml + fastapi + pytest available
$env:PYTHONPATH = "packages/schema;packages/edge-agent;packages/cloud"
pytest -q                      # uses pytest.ini (importlib mode, testpaths)
# to include the Postgres integration suite, also export a DSN:
$env:CAMAI_PG_DSN = "postgresql://user@host:5432/camai"
```
