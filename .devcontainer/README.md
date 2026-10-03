# Run CamAI in GitHub Codespaces

A one-click cloud dev environment that gives you **live forwarded URLs** for the
cloud API and dashboard — no local setup, no hosting account.

## Launch

On the repo page: **Code ▸ Codespaces ▸ Create codespace on main**
(or: https://codespaces.new/moonbitecoin/camai-saas).

The container builds and `postCreateCommand` installs the Python deps and the
dashboard's npm packages automatically.

## Start the whole stack (Docker)

In the Codespace terminal:

```bash
cp .env.example .env
# set a signing secret:
echo "NEXTAUTH_SECRET=$(openssl rand -base64 32)" >> .env
docker compose up -d --build
```

Codespaces forwards ports **3000** (dashboard) and **8000** (cloud API). Open the
**Ports** tab, click the 3000 forwarded URL → the live dashboard. Set that port's
visibility to **Public** in the Ports tab to share the URL with others.

Sign in at `/login` with the demo account `admin@camai.dev` / `demo`.

## Or run the dev servers directly (hot reload)

```bash
# cloud API
PYTHONPATH="packages/schema:packages/edge-agent:packages/cloud" \
  uvicorn app.main:app --app-dir packages/cloud --host 0.0.0.0 --port 8000 &

# dashboard (proxies /v1 -> the cloud API)
cd packages/dashboard && CAMAI_API_ORIGIN=http://localhost:8000 npm run dev
```

## Seed demo data

With the cloud API up:

```bash
PYTHONPATH=packages/schema python tools/seed_demo_account.py demo-tenant
PYTHONPATH=packages/schema python tools/seed_vertical_accounts.py
```

Then open the dashboard and switch accounts in the header.
