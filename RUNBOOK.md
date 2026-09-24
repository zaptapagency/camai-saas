# CamAI Runbook

Bring the whole system up from a clean checkout: cloud + dashboard, an edge agent
running against a camera, calibration, billing, and the production Postgres path.

For *what* CamAI is and *why* it's built this way, see [docs/PLAN.md](docs/PLAN.md);
for build status and what's left, [docs/ROADMAP.md](docs/ROADMAP.md).

Commands are **PowerShell (Windows)**, matching the dev environment. On macOS/Linux
use `source .venv/bin/activate` instead of `.venv\Scripts\activate` and `export VAR=…`
instead of `$env:VAR = …`.

---

## 0. Prerequisites

- **Python 3.10+** (required).
- **Node 18.17+** — only for the Next.js dashboard (`packages/dashboard`); the
  cloud already serves a no-build dashboard at `/`.
- **Git** — to version the repo (`git init` on first use).
- **PostgreSQL + TimescaleDB** — only for the production store; the default SQLite
  store needs nothing.
- **A camera** — any RTSP/HLS URL, a local video file, or a webcam. For a
  no-hardware test, a public live cam works (§5).

Three installable Python packages live under `packages/`: `schema` (shared
contract), `edge-agent`, `cloud`. `schema` is a local dependency, so **install it
first** in each environment.

---

## 1. One-time setup

Two virtual environments — one for the cloud, one for the edge agent. (They can
share one, but separate keeps the edge box lean.)

### Cloud

```powershell
cd packages\cloud
python -m venv .venv; .venv\Scripts\activate
pip install -e ..\schema
pip install -r requirements.txt
```

### Edge agent

```powershell
cd packages\edge-agent
python -m venv .venv; .venv\Scripts\activate
pip install -e ..\schema
pip install -e .                       # core pipeline (pulls torch/ultralytics)
pip install -e ".[calibrate,identity,discovery]"   # optional: wizard, mTLS, ONVIF
```

> `torch`/`ultralytics` is a large download. CPU works for testing; a GPU (Jetson
> Orin NX or an RTX card) is what hits the 5–15 FPS/stream target in the field.

---

## 2. Start the cloud + dashboard (SQLite, zero setup)

```powershell
cd packages\cloud; .venv\Scripts\activate
uvicorn app.main:app --port 8000
```

- Dashboard: <http://localhost:8000/>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Leave it running. Everything below talks to it.

---

## 3. Point an edge agent at a camera

Create a site config (start from [`packages/edge-agent/config.example.yaml`](packages/edge-agent/config.example.yaml)).
Minimal `site.yaml`:

```yaml
tenant_id: acme
site_id: store-01
device_id: edge-01                 # overridden by the minted identity if mTLS is on
cloud:
  ingest_url: "http://localhost:8000"
detector:
  device: cpu                      # or cuda:0
cameras:
  - id: front-door
    source: "rtsp://user:pass@192.168.1.50:554/Streaming/Channels/101"
    mode: retail                   # retail | parking | warehouse
    lines:
      - id: entrance
        a: { x: 0.15, y: 0.55 }
        b: { x: 0.85, y: 0.55 }
```

Run it:

```powershell
cd packages\edge-agent; .venv\Scripts\activate
python -m camai_edge.main --config site.yaml
```

Counts start flowing to the cloud; watch them on the dashboard (set the tenant to
`acme`). Add `--show` (single camera) to see the annotated video window, `--loop`
to loop a file source.

---

## 4. Calibrate a camera (draw lines/zones)

```powershell
cd packages\edge-agent; .venv\Scripts\activate
python -m camai_edge.calibrate --config site.yaml     # opens http://127.0.0.1:8900
```

Pick a camera, draw the entry/exit line (retail) or zones/spaces (parking,
warehouse) on the live snapshot, **Save** — geometry is written back to `site.yaml`.
Click **Discover cameras** to ONVIF-scan the LAN and list cameras to add.
Calibration is per-camera and is the single biggest driver of real-world accuracy.

---

## 5. No-hardware test: run against a public live camera

Validate the whole pipeline on real footage with zero privacy exposure. Use a
**fixed** cam (traffic/intersection or pedestrian crossing), not a rotating scenic
broadcast. Resolve a YouTube live cam to a stream URL first:

```powershell
pip install yt-dlp
$url = yt-dlp -g --extractor-args "youtube:player_client=android" `
  "https://www.youtube.com/watch?v=1EiC9bvVGnk"   # Jackson Hole Town Square
```

Put `$url` in your `site.yaml` `source:` (quote it), set `mode: parking` with a
zone over the road, and run `camai_edge.main` as in §3. Vehicles/pedestrians will
be detected, tracked, counted, and surfaced on the dashboard.

---

## 6. Full end-to-end: identity + zero-touch enrollment + fleet

This exercises every subsystem in one run.

**a. Mint an enrollment token** (server-side, in the cloud venv) and note it:

```powershell
cd packages\cloud; .venv\Scripts\activate
python -c "from app.device_registry import DeviceRegistry; import json; print(DeviceRegistry().create_enrollment_token('acme','store-01',3600))"
```

**b. Enable fleet in `site.yaml`:**

```yaml
fleet_enabled: true
fleet_poll_seconds: 60
```

**c. Run the edge agent with the token** (edge venv, with the `identity` extra):

```powershell
cd packages\edge-agent; .venv\Scripts\activate
python -m camai_edge.main --config site.yaml --enroll-token <TOKEN_FROM_STEP_A>
```

On startup the agent:
1. mints an EC key + cert (first boot) and derives a fingerprint `device_id`
   (`dev_…`) used for all events/heartbeats — the config `device_id` is a fallback;
2. POSTs its CSR + token to `/v1/devices/register` (zero-touch binding);
3. starts the FleetClient, pulling desired config + pinned release outbound-only.

**Verify (cloud side):**

```powershell
# device is bound (grab the dev_ id from the dashboard's device table or a heartbeat)
curl http://localhost:8000/v1/tenants/acme/devices
curl http://localhost:8000/v1/devices/<dev_id>
# push a desired config; the edge picks it up on its next poll
curl -X PUT "http://localhost:8000/v1/devices/<dev_id>/config?tenant_id=acme" `
  -H "content-type: application/json" -d '{\"config_version\":0,\"target_fps\":6}'
# live fleet alerts (stale devices / degraded streams)
curl http://localhost:8000/v1/tenants/acme/alerts
```

---

## 7. Billing (metered, per active camera)

```powershell
cd packages\cloud; .venv\Scripts\activate

# 1. Wire a tenant to its Stripe objects (admin API):
curl -X PUT "http://localhost:8000/v1/admin/tenants/acme/billing" `
  -H "content-type: application/json" `
  -d '{\"stripe_customer_id\":\"cus_x\",\"stripe_subscription_item_id\":\"si_x\",\"plan\":\"growth\"}'

# 2. Nightly job — count active cameras and report to Stripe.
python -m app.billing_job --dry-run                 # count only, no Stripe calls
$env:STRIPE_API_KEY = "sk_live_..."; python -m app.billing_job   # real run
```

A camera is "active" (billed) if it reported ≥1 event in the period. Usage is
reported with `action="set"`, so re-running never double-counts. Customers see their
count at `GET /v1/tenants/{id}/usage` (and on the dashboard's *Active cameras* tile).
Schedule `billing_job` once a day (Task Scheduler / cron / a cloud scheduler).

---

## 8. Production store: PostgreSQL + TimescaleDB

Default is SQLite. For production, apply the migrations and switch the backend:

```powershell
$env:CAMAI_PG_DSN = "postgresql://camai_app:***@db-host:5432/camai"
psql "$env:CAMAI_PG_DSN" -f packages\cloud\migrations\001_init.sql
psql "$env:CAMAI_PG_DSN" -f packages\cloud\migrations\002_continuous_aggregates.sql

$env:CAMAI_STORE = "postgres"
uvicorn app.main:app --port 8000        # from packages\cloud with its venv active
```

Run the API under a **non-owner** DB role so row-level security is enforced (table
owners bypass RLS). Details in [packages/cloud/migrations/README.md](packages/cloud/migrations/README.md).

---

## 9. Next.js dashboard (optional, productionized UI)

```powershell
cd packages\dashboard
copy .env.example .env.local            # set NEXT_PUBLIC_CAMAI_API_BASE=http://localhost:8000
npm install
npm run dev                             # http://localhost:3000
```

The cloud already serves a no-build dashboard at `/`; this is the richer,
multi-page app. It needs the cloud API to allow its origin (add CORS, or
reverse-proxy both under one origin). Auth/RBAC is stubbed — see its README.

---

## 10. Run the tests

```powershell
$env:PYTHONPATH = "packages\schema;packages\edge-agent;packages\cloud"
pytest -q                                # uses pytest.ini (importlib mode)
# include the Postgres integration suite:
$env:CAMAI_PG_DSN = "postgresql://user@host:5432/camai"; pytest -q
```

Expected: **135 passed, 1 skipped** with the first command — the skip is the
Postgres integration suite (`test_store_pg.py`), which needs `CAMAI_PG_DSN`. Set
it (the second command) to run those 7 tests too, for **142 passed**. If
`cryptography` isn't installed, the device-identity tests skip as well
(`pip install cryptography` to run them).

---

## 11. Environment variables

| Var | Where | Meaning |
|---|---|---|
| `CAMAI_STORE` | cloud | `sqlite` (default) or `postgres` |
| `CAMAI_DB` | cloud | SQLite file path (default `camai-cloud.db`) |
| `CAMAI_PG_DSN` | cloud | Postgres DSN when `CAMAI_STORE=postgres` |
| `CAMAI_DEVICE_REGISTRY_DB` | cloud | device-registry SQLite path |
| `CAMAI_FLEET_DB` | cloud | fleet desired-state SQLite path |
| `CAMAI_REQUIRE_MTLS` | cloud | `1` disables the dev `X-Device-Tenant` fallback (prod) |
| `STRIPE_API_KEY` | cloud | for a real `billing_job` run |
| `NEXT_PUBLIC_CAMAI_API_BASE` | dashboard | cloud API base URL |

---

## 12. Troubleshooting

- **Dashboard says "cannot reach API"** — the cloud server isn't running on :8000,
  or the tenant has no data yet. Check `/health`.
- **Edge: "no ingest_url configured → running offline"** — set `cloud.ingest_url`
  in `site.yaml`; until then events buffer in the local queue and print to console.
- **Edge: "device identity unavailable"** — install the extra:
  `pip install -e ".[identity]"` (needs `cryptography`). Without it the agent still
  runs using the config `device_id` (no mTLS).
- **Counts stay 0 on a live cam** — the camera may be showing an empty scene, or the
  line/zone geometry doesn't overlap where objects are. Recalibrate (§4); on a
  highway, occupancy shows the instantaneous count while parked/left events are
  debounced.
- **`pytest` "import file mismatch"** — run from the repo root so `pytest.ini`
  (importlib mode) is picked up.
- **ONVIF discovery finds nothing** — some networks block WS-Discovery multicast;
  fall back to entering the RTSP URL manually.

---

## 13. Teardown (dev)

```powershell
# stop the cloud server
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
# stop an edge agent: Ctrl-C in its terminal
```

SQLite DBs (`*.db`) and any minted `device/` identity dir are safe to delete to
reset local state.
