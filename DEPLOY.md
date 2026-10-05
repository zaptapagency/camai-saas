# Deploying CamAI

Two things deploy to **your** cloud: the **control plane** = the cloud API
(FastAPI) + the dashboard (Next.js). The **edge agent** runs *on-site* on each
customer's box (a GPU mini-PC next to their cameras) and only talks outbound to
your cloud — it is distributed as an image customers run, not something you host.

```
 customer site                         your cloud (this guide)
 ┌──────────────┐   counts/events     ┌─────────────┐   reads    ┌───────────┐
 │  edge agent  │ ──────────────────► │  cloud API  │ ◄───────── │ dashboard │
 │ (cameras,GPU)│   outbound only     │ (FastAPI)   │  /v1 proxy │ (Next.js) │
 └──────────────┘                     └──────┬──────┘            └───────────┘
                                             │
                                        Postgres / Timescale
```

---

## Option A — one host with Docker Compose (fastest)

Good for a VPS, a staging box, or a laptop.

```bash
cp .env.example .env
# set at least NEXTAUTH_SECRET:  openssl rand -base64 32
docker compose up -d --build
```

- Dashboard → http://localhost:3000  (sign in at `/login`, demo: `admin@camai.dev` / `demo`)
- Cloud API → http://localhost:8000  (health at `/health`)

Defaults to the SQLite dev store so it works immediately. For production, see
**Production hardening** below (switch to Postgres).

## Option B — Render blueprint (managed, near one-click)

The repo ships [`deploy/render.yaml`](deploy/render.yaml): cloud API + dashboard +
a managed Postgres.

1. In [Render](https://render.com): **New → Blueprint**, point it at this repo.
2. Fill the `sync:false` secrets (`STRIPE_API_KEY`, `GOOGLE_CLIENT_ID/SECRET`).
3. **Apply.** `NEXTAUTH_SECRET` is generated, `CAMAI_PG_DSN` is wired from the DB.

## Option C — split (dashboard on Vercel, cloud elsewhere)

- **Cloud API:** deploy `packages/cloud/Dockerfile` to Render / Fly.io / a VM. Set
  `CAMAI_STORE=postgres` + `CAMAI_PG_DSN` and a managed Postgres/Timescale.
- **Dashboard on Vercel:** import `packages/dashboard`. Set the build-time env
  `CAMAI_API_ORIGIN=https://<your-cloud-api-host>` (the `/v1/*` proxy target),
  plus `NEXTAUTH_SECRET` and `NEXTAUTH_URL`.

> `CAMAI_API_ORIGIN` is baked at **build time** (Next resolves rewrites then), so
> set it as a build env/arg, not just a runtime variable.

## Wire the "Launch 3-hour live demo" button

The public landing page (`docs/`, served via GitHub Pages) has a **Launch 3-hour
live demo** button. It links to `<dashboardUrl>/demo`, a dashboard route that mints
a session (`POST /v1/demo/start`) *same-origin* — the dashboard proxies `/v1/*` to
the cloud API, so there is **no CORS to configure** — and opens the dashboard pinned
to `demo-master` with a live countdown.

To make it work against your deployment:

1. **Deploy the control plane** (Option A/B/C above) so the dashboard is reachable
   at a public URL, with `CAMAI_API_ORIGIN` pointing at the cloud API.
2. **Seed the demo tenant** once (and on each reset):
   ```bash
   # against the deployed cloud API
   CAMAI_DEMO_HOURS=3  # optional; cloud already defaults to 3h
   PYTHONPATH=packages/schema python tools/seed_demo_account.py demo-master
   ```
3. **Point the page at your dashboard**: edit `docs/config.js` →
   `dashboardUrl: "https://<your-dashboard-host>"`, commit, and GitHub Pages
   redeploys. (Or test any deployment ad-hoc with `?app=<dashboard-url>` on the page.)

That's it — the button now opens a real 3-hour demo. For live video, also run the
edge agent against public cams (`tools/resolve_streams.py`, see the README).

## The edge agent (customer side)

Build and ship [`packages/edge-agent/Dockerfile`](packages/edge-agent/Dockerfile)
to customer boxes (add an NVIDIA base + `--gpus all` for GPU). Each runs with a
site config whose `cloud.ingest_url` points at your deployed cloud API. mTLS
device identity is minted on first boot.

---

## Production hardening (do before real traffic)

- [ ] **Postgres/Timescale**, not SQLite: set `CAMAI_STORE=postgres` + `CAMAI_PG_DSN`,
      and apply the DDL in `packages/cloud/migrations/` (hypertable + continuous
      aggregates + row-level security).
- [ ] **Secrets**: a strong `NEXTAUTH_SECRET`; real `STRIPE_API_KEY`; never commit `.env`.
- [ ] **Real SSO**: set `GOOGLE_CLIENT_ID/SECRET` (or wire your IdP) to replace the
      demo Credentials login; see `packages/dashboard/lib/auth.ts`.
- [ ] **Enforce device identity on ingest**: put the cloud behind a TLS-terminating
      proxy and set `CAMAI_REQUIRE_MTLS=1` (see `packages/cloud/app/security.py`).
- [ ] **Object storage** for snapshots/clips instead of the local disk volume
      (`CAMAI_SNAPSHOT_DIR`); the schema's `clip_ref` is reserved for this.
- [ ] **TLS + a domain**, DB backups, and log/metrics shipping.
- [ ] Remove or lock down the **demo seed accounts** (`tools/seed_*`), which are for
      local demos only.

## What only you can do

Going live means connecting **your** hosting account and holding **your** secrets
(Render/Vercel/Fly login, managed Postgres, Stripe, an SSO app). Those stay with
you — run Option A/B/C above with your account and credentials. The repo provides
everything else: images, compose, the Render blueprint, and this guide.
