"""CamAI cloud control plane — ingest slice.

The first cloud endpoint the edge agent talks to. Deliberately minimal: it proves
the edge<->cloud contract end to end (batched, idempotent, outbound-only) and
gives the dashboard something to read. Auth (mTLS device certs), TimescaleDB,
row-level tenant isolation, billing, and fleet management build on top of this.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.responses import HTMLResponse, JSONResponse

from camai_schema import EventBatch, Heartbeat, IngestAck

from app.billing import month_bounds
from app.store_factory import get_store

app = FastAPI(title="CamAI Cloud (ingest slice)", version="0.1.0")
store = get_store()

_STATIC = Path(__file__).parent / "static"

# --- Feature routers -------------------------------------------------------- #
# Additive surfaces built out on top of the ingest slice. Ingest itself still
# uses the body-tenant seam below; switching it to cert-derived identity
# (app.security.current_device) is a deploy-time step tied to a TLS-terminating
# proxy, gated by CAMAI_REQUIRE_MTLS — see app/security.py.
from app.billing_admin_router import router as billing_admin_router
from app.device_registry import router as device_registry_router
from app.fleet_router import configure as _configure_fleet
from app.fleet_router import router as fleet_router

app.include_router(device_registry_router)
app.include_router(fleet_router)
app.include_router(billing_admin_router)

# Fleet alerts/rollout derive device liveness from the heartbeats already in the
# main event store.
_configure_fleet(heartbeat_accessor=store.device_health)


def _resolve_tenant(body_tenant: str, cert_tenant: str | None) -> str:
    """In production the tenant is derived from the authenticated device cert, and
    the body value is only a cross-check. Here we accept the body but keep the seam
    so wiring real mTLS later is a one-line change.
    """
    if cert_tenant and cert_tenant != body_tenant:
        # Never let an edge box write into another tenant's data.
        raise ValueError("tenant mismatch between device identity and payload")
    return cert_tenant or body_tenant


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """Serve the minimal pilot dashboard (single self-contained page)."""
    index = _STATIC / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>CamAI</h1><p>dashboard asset missing</p>", status_code=404)
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "camai-cloud", "version": app.version}


@app.post("/v1/ingest/events", response_model=IngestAck)
def ingest_events(
    batch: EventBatch,
    x_device_tenant: str | None = Header(default=None),
) -> IngestAck:
    tenant = _resolve_tenant(batch.tenant_id, x_device_tenant)
    # Enforce that every event in the batch belongs to the resolved tenant.
    events = [e for e in batch.events if e.tenant_id == tenant]
    rejected = len(batch.events) - len(events)
    accepted, duplicates = store.insert_events(events)
    return IngestAck(
        batch_id=batch.batch_id,
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
    )


@app.post("/v1/ingest/heartbeat")
def ingest_heartbeat(
    hb: Heartbeat,
    x_device_tenant: str | None = Header(default=None),
) -> dict:
    _resolve_tenant(hb.tenant_id, x_device_tenant)
    store.upsert_heartbeat(hb)
    return {"status": "ok"}


# --- Read side (thin, for the dashboard to poll during the pilot) ------------

@app.get("/v1/tenants/{tenant_id}/events")
def recent_events(tenant_id: str, limit: int = 100) -> JSONResponse:
    return JSONResponse(store.recent_events(tenant_id, limit=min(limit, 1000)))


@app.get("/v1/tenants/{tenant_id}/devices")
def device_health(tenant_id: str) -> JSONResponse:
    return JSONResponse(store.device_health(tenant_id))


@app.get("/v1/tenants/{tenant_id}/summary")
def summary(tenant_id: str) -> JSONResponse:
    """Aggregated tiles for the dashboard (occupancy, parking, devices, feed)."""
    data = store.summary(tenant_id)
    # Active-camera count for the current billing period (the billed unit).
    start, end = month_bounds()
    active = store.active_camera_ids(tenant_id, start.isoformat(), end.isoformat())
    data["totals"]["active_cameras"] = len(active)
    return JSONResponse(data)


@app.get("/v1/tenants/{tenant_id}/usage")
def usage(tenant_id: str) -> JSONResponse:
    """Customer-facing usage view: active cameras this billing period + plan.

    Transparency here is the cheapest way to avoid billing disputes.
    """
    start, end = month_bounds()
    cameras = store.active_camera_ids(tenant_id, start.isoformat(), end.isoformat())
    account = store.get_billing_account(tenant_id)
    return JSONResponse({
        "tenant_id": tenant_id,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "active_cameras": len(cameras),
        "camera_ids": cameras,
        "plan": (account or {}).get("plan"),
    })
