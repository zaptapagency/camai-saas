# CamAI

Turn existing CCTV into a metered analytics product — retail foot traffic, warehouse
counting, and parking occupancy — from one edge-first vision pipeline, billed per
camera per month.

> **Architecture is edge-first.** A small GPU box on-site runs YOLO detection and
> tracking directly on the customer's RTSP streams. Only counts, events, and short
> clips (never continuous raw video) leave the building. See [docs/PLAN.md](docs/PLAN.md)
> for the full product/production plan.

## Repository layout

This is a monorepo mirroring the three deployable layers from the plan.

```
packages/
  schema/       Shared event contract (edge -> cloud). The single source of truth
                for what an event looks like. Both sides depend on it.
  edge-agent/   On-site agent: RTSP/video ingest -> YOLO detect -> track ->
                mode-configurable counting (retail / warehouse / parking) ->
                batched, outbound-only sync to the cloud. **Built and runnable.**
  cloud/        Cloud control plane (FastAPI): ingest endpoint, health. Minimal
                first slice — auth, Timescale, billing, fleet mgmt come next.
  dashboard/    Customer web app (Next.js). Placeholder — see its README.
docs/
  PLAN.md       The enterprise production plan (source of truth for scope).
```

## What's built so far

The **edge vision pipeline** is the crown jewel and the biggest accuracy risk, so
it's the first thing built end to end:

- RTSP / file / webcam ingest via OpenCV (`camai_edge/ingest.py`)
- YOLOv11 person/vehicle detection (`camai_edge/detect.py`)
- Persistent-ID tracking via the detector's built-in ByteTrack (`camai_edge/track.py`)
- Sixteen counting modes on **one** pipeline, switched by config:
  - `retail`   — virtual line-crossing at entrances/exits + zone dwell
  - `parking`  — per-zone occupancy via static ROI + vehicle detection
  - `warehouse`— zone-based object counting (beta; hardest CV problem)
  - `queue`    — queue length + per-person wait time (dwell) in a waiting zone
  - `safety`   — PPE compliance (helmet/vest) in a required-equipment zone (needs a PPE-trained model)
  - `traffic`  — directional vehicle flow across a line (smart city); optional class breakdown
  - `staffing` — workstation coverage (is a work area staffed?); anonymous, station-level
  - `capacity` — live headcount vs a configured zone occupancy limit → breach alerts (Tier 1)
  - `proximity`— forklift↔pedestrian near-miss alerts (Tier 2; needs a forklift class)
  - `fire`     — fire/smoke hazard alerts (Tier 3; needs a fire/smoke-trained model)
  - `thermal`  — thermal fever/overheat screening (Tier 4; needs a thermal camera)
  - `drive_thru` — vehicle service-time per lane (dwell); QSR drive-thru SLAs (Tier 1)
  - `loitering` — prolonged person presence in a zone → alert (Tier 1)
  - `intrusion` — person in a restricted / after-hours zone → alert (Tier 1)
  - `crowd_density` — crowd headcount vs a crush-risk threshold → alert (Tier 1)
  - `tailgating` — multiple people through a secure line together → alert (Tier 1)
- Local event queue + batched, outbound-only sync (`camai_edge/events.py`, `sync.py`)
- A single YAML config per site describing cameras, zones, and lines

Plus a **minimal cloud ingest API + served dashboard** (`packages/cloud`) and a
**shared schema** (`packages/schema`) both sides import.

The **calibration wizard** (`camai_edge.calibrate`) — the biggest accuracy lever —
is built: a local web tool that grabs a live snapshot per camera, lets you draw
entry/exit lines and zones by clicking, and writes the geometry back into the site
config. The **dashboard** (served by the cloud app at `/`) shows live occupancy
tiles, a per-zone occupancy chart, parking states, device health, and an event feed.

> **Bringing the whole system up?** Follow [RUNBOOK.md](RUNBOOK.md) — one
> copy-paste sequence for cloud + dashboard, an edge agent on a camera,
> calibration, identity/fleet, billing, and the Postgres path.

> **How accuracy improves over time?** See [docs/flywheel.md](docs/flywheel.md) —
> the live-camera → label → measured-accuracy → retrain → promote loop (opt-in,
> privacy-first), and how to run `camai-retrain`.

## Quick start (edge pipeline against a video file)

No camera or GPU required to try it — a CPU and any video file work (slower FPS).

```bash
cd packages/edge-agent
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ../schema        # shared event contract (local package) first
pip install -e .                # then the edge agent (pulls torch/ultralytics)
# edit config.example.yaml -> point a camera at a local .mp4 and draw a line
python -m camai_edge.main --config config.example.yaml --show
```

Events print to the console and (if a cloud URL is configured) sync to the ingest API.

To run the cloud ingest API + dashboard locally:

```bash
cd packages/cloud
python -m venv .venv && .venv\Scripts\activate
pip install -e ../schema
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then open **http://localhost:8000/** for the dashboard. Point the edge agent at it
by setting `cloud.ingest_url: "http://localhost:8000"` in the site config.

## Calibrating a camera (draw lines/zones)

```bash
cd packages/edge-agent
pip install -e ".[calibrate]"          # adds the wizard's web server
camai-calibrate --config config.example.yaml     # opens http://127.0.0.1:8900
```

Pick a camera, draw the entry/exit line (retail) or zones/spaces (parking,
warehouse) on the live snapshot, and Save — the geometry is written back to the
YAML the agent reads. Calibration is per-camera and is the single biggest driver
of real-world accuracy.

## Testing against a public camera (before a private one)

The pipeline accepts any RTSP/HLS URL as a camera `source`, so you can validate the
whole system on a public feed — same code path, real people/vehicles, zero privacy
exposure — before pointing it at a customer's private camera. Use a **fixed** cam
(a traffic/intersection or pedestrian-crossing feed), not a rotating scenic
broadcast. For a YouTube live cam, resolve a direct stream URL first:

```bash
yt-dlp -g --extractor-args "youtube:player_client=android" "<youtube-live-url>"
```

Put that URL in the site config's `source:` (quoted), run `camai-edge --config …`,
and watch counts appear on the dashboard. This was used to verify the pipeline on
a live intersection cam (vehicle detection + occupancy + parked/left events) and a
highway cam (tracking with persistent IDs) before any private deployment.

## Status & next steps

This is a **foundation**, not the finished pilot. See [docs/ROADMAP.md](docs/ROADMAP.md)
for the ordered build-out (calibration wizard, Timescale schema, Stripe metering,
fleet health, dashboard, ONVIF auto-discovery).

## License

Proprietary — © Alaqtar / CamAI. All rights reserved.
