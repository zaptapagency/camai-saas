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

import base64

from fastapi import Depends, FastAPI, Header, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from camai_schema import EventBatch, Heartbeat, IngestAck

from app import audit
from app import demo
from app import snapshots
from app.billing import month_bounds
from app.rbac import Role, require_role
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


@app.get("/v1/tenants/{tenant_id}/audit")
def tenant_audit(
    tenant_id: str,
    limit: int = 100,
    role: Role = Depends(require_role(Role.analyst)),
) -> JSONResponse:
    """Tenant-scoped audit trail, newest first.

    Reading the trail is itself privileged — it exposes who did what — so it is
    gated at ``analyst`` (above plain ``viewer`` dashboard access) while the
    mutations that write to it require ``admin``. See :mod:`app.audit`.
    """
    return JSONResponse(audit.list_entries(tenant_id, limit=min(limit, 1000)))


# --- Accuracy flywheel: snapshots, labels, measured accuracy ----------------
# The edge agent pushes a frame + its predicted count; a human labels the true
# count; we compute per-tenant/per-mode accuracy from the resulting samples.
# Standalone store (app.snapshots), independent of the main event store.


class SnapshotIn(BaseModel):
    tenant_id: str
    camera_id: str
    mode: str
    ts: str
    predicted_count: int
    image_b64: str


class LabelIn(BaseModel):
    actual_count: int


@app.post("/v1/ingest/snapshot")
def ingest_snapshot(body: SnapshotIn) -> dict:
    image_bytes = base64.b64decode(body.image_b64)
    snapshots.save_snapshot(
        body.tenant_id,
        body.camera_id,
        body.mode,
        body.ts,
        body.predicted_count,
        image_bytes,
    )
    return {"ok": True}


@app.get("/v1/tenants/{tenant_id}/cameras/{camera_id}/snapshot.jpg")
def snapshot_image(tenant_id: str, camera_id: str):
    img = snapshots.latest_image(tenant_id, camera_id)
    if img is None:
        return Response("no snapshot", status_code=404, media_type="text/plain")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/v1/tenants/{tenant_id}/cameras/{camera_id}/snapshot")
def snapshot_meta(tenant_id: str, camera_id: str) -> JSONResponse:
    meta = snapshots.latest_meta(tenant_id, camera_id)
    if meta is None:
        return JSONResponse({"detail": "no snapshot"}, status_code=404)
    return JSONResponse(meta)


@app.post("/v1/tenants/{tenant_id}/cameras/{camera_id}/label")
def add_label(tenant_id: str, camera_id: str, body: LabelIn) -> JSONResponse:
    sample = snapshots.add_label(tenant_id, camera_id, body.actual_count)
    if sample is None:
        return JSONResponse({"detail": "no snapshot to label"}, status_code=404)
    return JSONResponse(sample)


@app.get("/v1/tenants/{tenant_id}/accuracy")
def tenant_accuracy(tenant_id: str) -> JSONResponse:
    return JSONResponse(snapshots.accuracy(tenant_id))


# --- Time-boxed demo sessions ------------------------------------------------
# A prospect launches a shareable, read-only demo of the `demo-master` tenant for
# a fixed window (default 3h). Tokens are session handles, not credentials — they
# authorize viewing the demo tenant's aggregates for the window and nothing else.


@app.post("/v1/demo/start")
def demo_start() -> JSONResponse:
    """Mint a fresh time-boxed demo session (token + expiry)."""
    return JSONResponse(demo.start())


@app.get("/v1/demo/session")
def demo_session(token: str) -> JSONResponse:
    """Validate a demo token; report remaining time / expiry for the countdown."""
    view = demo.session(token)
    if view is None:
        return JSONResponse({"detail": "unknown demo token"}, status_code=404)
    if view["expired"]:
        return JSONResponse(view, status_code=410)  # Gone — window closed
    return JSONResponse(view)


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
