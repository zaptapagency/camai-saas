# CamAI — Enterprise Production Plan

_Sep 23, 2026 · @Usman Safdar — source of truth for scope. This file preserves the
strategy plan; implementation status lives in [ROADMAP.md](ROADMAP.md)._

## Executive summary

CamAI turns existing CCTV into a metered analytics product: retail foot traffic,
warehouse counting, and parking occupancy, all from the same edge-first vision
pipeline, billed per camera per month.

Architecture is edge-first: a small GPU box installed on-site runs YOLO detection
and tracking directly on the customer's RTSP streams, and only counts, events, and
short clips (never continuous raw video) leave the building. This is what makes the
product enterprise-sellable — retail and warehouse customers will not ship raw
camera feeds to a third-party cloud — and it keeps cloud compute cost near zero,
which is what makes $10–40/camera/month economically viable.

Production scope for v1 is a small live pilot: 5–20 cameras across 2–3 paying or
design-partner customers, spanning all three verticals from day one on one
configurable pipeline (mode = retail / warehouse / parking), live in 6–10 weeks.
The architecture scales to hundreds of cameras without a rebuild, but the pilot
stays small so accuracy and reliability are proven before sales scale.

## System architecture

Three layers, each independently deployable:

- **Edge agent** (on-site, one per site or per NVR cluster): pulls RTSP from
  existing cameras/NVR, runs the vision pipeline locally, buffers events, syncs to
  the cloud over an outbound-only encrypted connection (no inbound ports opened on
  customer networks).
- **Cloud control plane**: multi-tenant API + Postgres (TimescaleDB extension for
  time-series counts), ingests events from every edge agent, stores aggregated
  analytics (never raw video by default), runs the dashboard backend, billing,
  alerting, and fleet management (device health, config push, model updates).
- **Customer dashboard**: web app (Next.js) per tenant — live occupancy, historical
  trends, heatmaps, alerts, camera health, exportable reports; role-based access
  (admin, site manager, viewer).

**Data flow:** camera → edge agent (inference + tracking) → local event queue →
batched sync (every 10–60s) → cloud ingest API → Timescale + object storage
(optional clip snapshots only) → dashboard/API/webhooks.

**Multi-tenancy:** every row is tenant-scoped (`tenant_id`), Postgres row-level
security enforces isolation, one edge agent is registered to exactly one
tenant/site and authenticates with a per-device certificate, not a shared API key.

## Vision pipeline

One pipeline, three configurable modes, so the same edge agent and model family
serve all verticals from day one:

| Component | Choice | Notes |
|---|---|---|
| Detector | YOLOv11 (Ultralytics), person/vehicle classes | Pretrained COCO baseline; fine-tune per site once pilot footage exists |
| Tracker | ByteTrack or BoT-SORT | Persistent IDs across frames — required for counting, dwell, not double-counting |
| Retail foot traffic | Virtual line-crossing at entrances/exits + zone dwell | Counts ins/outs, occupancy, dwell per zone |
| Warehouse counting | Zone-based object counting with occlusion-aware tracking | Hardest of the three — longer tuning window, possibly a second model |
| Parking | Per-space or per-zone occupancy via static ROI + vehicle detection | Simplest — static camera, static zones, high baseline accuracy |

Calibration is **per-camera**, not per-vertical: each install requires a short
on-site calibration pass (draw zones/lines, verify FPS and angle) before go-live —
the single biggest driver of real-world accuracy.

Inference target: 5–15 FPS per stream is sufficient for counting — this keeps one
edge GPU box able to handle 4–8 simultaneous streams.

## Edge hardware & fleet management

Reference device: NVIDIA Jetson Orin NX (16GB) or a small x86 box with an entry GPU
(e.g. RTX A2000) — 4–8 streams at 5–15 FPS. One box per site for the pilot.

Fleet operations from the cloud control plane: remote config push; OTA model/agent
updates with staged (canary) rollout; heartbeat + health metrics (CPU/GPU temp,
disk, stream FPS, last-seen) with alerting; local buffering (hours) so a brief
connectivity drop doesn't lose events. This layer is what makes per-camera billing
operationally sane at scale.

## Backend, APIs & dashboard

FastAPI (shares language with the vision pipeline) or Node/NestJS; Postgres +
TimescaleDB; Redis for real-time state and job queues; S3-compatible object storage
for optional clip snapshots.

Core data model: tenant → site → camera → zone/line → events (typed: entry, exit,
occupancy_sample, vehicle_parked, count_delta) → hourly/daily aggregates.

APIs: REST/GraphQL for the dashboard, a dedicated mTLS append-only ingest endpoint
for edge agents, webhooks for customer integrations, and a public API.

## Billing & metering

Unit of billing: active camera per month (active = its edge agent reported at least
one event in the period). Stripe Billing with metered usage; a nightly job counts
active cameras per tenant and reports via `usage_records`.

Plans (tune during pilot): Starter (PAYG per camera), Growth (volume tiers),
Enterprise (annual, SSO, SLA — sold outside self-serve). Lock the count at period
start to avoid retroactive disputes; customer-facing usage dashboard for transparency.

## Security, privacy & compliance

Edge-first is the primary privacy control: raw video stays on the customer network;
only counts/events (and opt-in short low-res clips) leave. No facial recognition /
biometric identification in the default product — tracking uses anonymous
within-session re-identification only. Per-tenant retention (default ~90d
aggregates, ~7d clips); encryption in transit (mTLS edge→cloud) and at rest;
SSO/SAML + RBAC + audit log for enterprise; DPA template + sub-processor list.
SOC 2 Type II once there are paying enterprise customers — start the control
framework early.

## Reliability & operations

Pilot SLA: 99% cloud uptime, best-effort on edge (fleet health monitoring is the
real commitment). APM on the cloud (Sentry + Grafana/Prometheus); edge heartbeat /
FPS / temp / disk. Alert cloud incidents to on-call; alert camera/device issues to
the customer dashboard. Runbooks per failure class; status page; postmortems.
Automated Postgres PITR backups; IaC; defined RPO/RTO (~1h/4h for pilot).

## CI/CD & environments

dev → staging (mirrors prod) → production, IaC (Terraform). GitHub Actions:
lint/type-check/unit → integration against staging DB → deploy on merge with
automated rollback. Edge agent releases are the higher-risk path: version-pinned,
canary rollout, auto-rollback on failed health checks, pin a pilot customer to a
known-good version. Testing: unit tests against recorded reference clips
(regression-test accuracy), load tests on ingest per new site, a staging "digital
twin" site.

## Pilot rollout plan (6–10 weeks)

| Weeks | Milestone |
|---|---|
| 1–2 | Core build: edge agent (RTSP + YOLO + tracker + event emission), cloud ingest API, minimal data model |
| 3–4 | Dashboard v1, Stripe metered billing end to end, edge fleet health/heartbeat |
| 5 | Lab validation: all three modes against recorded + live footage; fix obvious gaps |
| 6–7 | First site install: hardware, on-site calibration, live data, customer walkthrough |
| 8 | Second and third sites; accuracy tuning per site |
| 9–10 | Stabilization; finalize SLA/DPA; collect accuracy benchmarks |

**Exit criteria for "production-grade":** 2+ weeks stable uptime across sites,
documented accuracy (±5% on foot traffic vs. manual audit), correct billing for a
full cycle, no unplanned edge outage >1h without alerting firing.

## Cost model & unit economics

| Item | Approx. cost | Notes |
|---|---|---|
| Edge box | $600–$1,200 one-time/site | Amortize 2–3 yrs or pass through as setup fee |
| Cloud infra | $200–$800/mo at pilot scale | Sub-linear — storing counts, not video |
| Per-camera marginal | Near-zero | Core edge-first advantage |
| Install/calibration | $150–$400 one-time/site | Falls with remote-calibration workflow |
| Support/ops | Scales with device count, not camera count | Keep cameras-per-device high |

At $15–$30/camera/month with near-zero marginal cloud cost, gross margin per camera
is high once past the one-time hardware/install. Price hardware separately or
amortize into the first 6–12 months; don't hide it in the per-camera rate.

## Risks & mitigations (summary)

- Per-site accuracy variance → mandatory calibration; publish real pilot numbers.
- Warehouse mode lags → ship as beta pricing/SLA until it clears the accuracy bar.
- Enterprise privacy review → edge-first + no-raw-video is the answer; DPA ready.
- Edge hardware failure → fleet health + OTA from week one; keep spares.
- Low camera density per site → set a minimum monthly fee per site.
- Established competitors → differentiate on "works with existing CCTV" + multi-vertical.

## Universal camera & NVR compatibility

Solve it once at the protocol layer: ONVIF Profile S/T auto-discovery (~80%+ of
commercial cameras/NVRs), raw RTSP/RTMP URL fallback (universal), NVR/VMS bridge
(one connection, many cameras), analog via existing IP encoder. Certify against the
5–10 dominant brands during the pilot; ship a published, growing compatibility list
rather than an unqualified "any camera" claim.

## Plug-and-play, self-serve onboarding

Sign up → install lightweight agent (appliance or Docker) → ONVIF auto-discovery →
guided calibration wizard (draw lines/zones in the dashboard) → live in minutes with
a data-quality indicator → self-serve Stripe billing. Site visits become the
exception (large/legacy/analog/enterprise white-glove). Auto-discovery + self-serve
calibration is the single highest-leverage engineering investment for SaaS margins.

## Honest read on scale

A real, large market (retail, logistics, parking, smart cities, workplace). No one
has won it globally with a horizontal, camera-agnostic, self-serve platform. What
compounds: (1) win the pilot on accuracy/reliability in writing; (2) nail
self-serve camera-agnostic onboarding; (3) expand verticals on the same pipeline;
(4) build an installer/integrator channel; (5) defensibility from data, integration
breadth, and switching cost. Treat "billion-dollar" as a ceiling this architecture
doesn't block, not a target the roadmap promises.
