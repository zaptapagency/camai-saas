# CamAI Dashboard

The per-tenant customer web app: a **Next.js 14 (App Router) + TypeScript +
Tailwind** client for the CamAI cloud API. It replaces the single-file SPA served
at `/` by `packages/cloud` with a real, multi-page, typed front end.

## What's here

| Route      | Purpose                                                                 |
| ---------- | ----------------------------------------------------------------------- |
| `/`        | Overview: live occupancy tiles (incl. **active cameras billed**), per-zone occupancy chart, parking states, device health, and a live event feed. |
| `/devices` | Fleet health: online/offline roll-up + full per-device table (agent, queued events, per-camera FPS with dark-stream warnings, CPU/GPU/disk). |
| `/usage`   | Billing/usage: metered active-camera count, plan, billing period, and (admin only) the exact camera ids billed. |

Everything is tenant-scoped and polls live via **TanStack Query** on an
operator-selectable cadence (5s / 10s / 30s / off). Light/dark follows the OS
colour scheme; layout is responsive down to phone width.

## Setup

Node is required (>= 18.17). From this directory:

```bash
cp .env.example .env.local        # set NEXT_PUBLIC_CAMAI_API_BASE
npm install
npm run dev                       # http://localhost:3000
```

Other scripts: `npm run build` / `npm run start` (production), `npm run
typecheck`, `npm run lint`.

## Environment

| Var                          | Meaning                                                                 |
| ---------------------------- | ----------------------------------------------------------------------- |
| `NEXT_PUBLIC_CAMAI_API_BASE` | Base URL of the cloud API, e.g. `http://localhost:8000`. Empty = same-origin (when the dashboard is reverse-proxied in front of the API). |

### Running against the cloud API

1. Start the cloud service (from `packages/cloud`):
   `uvicorn app.main:app --reload --port 8000`.
2. Set `NEXT_PUBLIC_CAMAI_API_BASE=http://localhost:8000` in `.env.local`.
3. `npm run dev`, then pick a tenant (defaults to `demo-tenant`) in the header.

Because the browser calls the API cross-origin in dev, the cloud FastAPI app
needs CORS to allow the dashboard origin (`http://localhost:3000`) — add
`CORSMiddleware` there, or reverse-proxy both under one origin and leave
`NEXT_PUBLIC_CAMAI_API_BASE` empty.

## Endpoints read

All read-only GETs (the dashboard never writes to the cloud):

- `GET /v1/tenants/{id}/summary` — tiles, occupancy, parking, devices, recent feed.
- `GET /v1/tenants/{id}/events?limit=` — raw event list.
- `GET /v1/tenants/{id}/devices` — heartbeats / fleet health.
- `GET /v1/tenants/{id}/usage` — active cameras, plan, billing period.

TS types for these live in `lib/api.ts` and mirror `packages/schema/camai_schema.py`.

## Architecture

- `lib/api.ts` — typed fetch client + wire types; one `authHeaders()` seam for real auth.
- `lib/queries.ts` — TanStack Query hooks bound to the active tenant + cadence.
- `lib/tenant.tsx` — active tenant + refresh interval (persisted to localStorage).
- `lib/roles.tsx` — stub RBAC role context and capability map.
- `components/` — `Tile`, `Card`, `OccupancyChart` (Recharts), `DeviceTable`,
  `ParkingTable`, `EventFeed`, RBAC-aware `Nav`, and `RequireCapability` guard.

## What's stubbed

- **Auth / SSO.** There is no real sign-in. A role switcher in the header
  (`admin` / `manager` / `viewer`) stands in for an identity provider, persisted
  in localStorage. Wiring real auth means: (1) obtain the session + role from an
  IdP (SSO/SAML/OIDC), (2) set the role in `RoleProvider` from the session claim,
  (3) attach the token in `authHeaders()` in `lib/api.ts`. Every `can(...)` call
  site and `<RequireCapability>` guard then works unchanged.
- **RBAC is UX-only.** Client-side gating hides pages/fields; it is **not**
  security. The cloud API must enforce authorisation server-side per session.
- **Tenant selection** is a free-text box (admin-only). Under real auth the
  tenant derives from the session and the box becomes an admin override.

## Not yet built (roadmap)

Historical trend charts, per-camera heatmaps, alert configuration, CSV/PDF
export, and the guided calibration wizard (see `docs/ROADMAP.md`). The calibration
wizard currently lives in the edge agent (`packages/edge-agent` `calibrate.py`).
