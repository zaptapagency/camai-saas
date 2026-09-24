"""Fleet management HTTP surface — a self-contained APIRouter.

Wire-up (owned by the orchestrator, in ``app/main.py``)::

    from app.fleet_router import router as fleet_router
    app.include_router(fleet_router)

Endpoints (all cloud-driven; the edge only ever *pulls* — outbound-only):

* ``GET  /v1/devices/{device_id}/config``  — edge pulls its desired config
* ``PUT  /v1/devices/{device_id}/config``  — operator sets it
* ``GET  /v1/devices/{device_id}/release`` — edge pulls its pinned version
* ``PUT  /v1/devices/{device_id}/release`` — operator pins it
* ``PUT  /v1/tenants/{tenant_id}/rollout`` — operator starts a canary rollout
* ``GET  /v1/tenants/{tenant_id}/alerts``  — stale devices / degraded streams,
  derived on read from the heartbeats already in the main store

Desired state lives in this module's own tiny SQLite store (``FleetStore``) so it
does not touch ``app.store``; the two backends stay independently swappable.
Heartbeats are read through a small *injected accessor* (default: the main store's
``device_health``) so this router never imports a concrete store and tests can feed
synthetic heartbeats. Call :func:`configure` to inject either seam.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.fleet import (
    DesiredConfig,
    DesiredRelease,
    ReleaseChannel,
    compute_alerts,
    resolve_release,
    select_canary,
)

# A callable that returns the list of (dict) heartbeats for a tenant, newest
# first — exactly the shape ``Store.device_health(tenant_id)`` already returns.
HeartbeatAccessor = Callable[[str], list[dict]]


# --------------------------------------------------------------------------- #
# Desired-state store (self-contained; does NOT touch app.store)
# --------------------------------------------------------------------------- #

class FleetStore:
    """SQLite-backed store for per-device desired config and release pins.

    Kept in this module on purpose: fleet desired-state has a different lifecycle
    from the append-only event stream (it is read-mostly, last-write-wins,
    per-device) and keeping it separate means neither store constrains the other's
    migration to Postgres later. ``tenant_id`` is recorded alongside each device so
    a rollout can enumerate a tenant's devices even before any heartbeat arrives.
    """

    def __init__(self, path: str | Path = "camai-fleet.db") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS desired_config (
                device_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                payload   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS desired_release (
                device_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                payload   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cfg_tenant ON desired_config (tenant_id);
            CREATE INDEX IF NOT EXISTS idx_rel_tenant ON desired_release (tenant_id);
            """
        )
        self._conn.commit()

    # -- config ------------------------------------------------------------- #

    def get_config(self, device_id: str) -> Optional[DesiredConfig]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM desired_config WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return DesiredConfig.model_validate_json(row[0]) if row else None

    def set_config(self, device_id: str, tenant_id: str, config: DesiredConfig) -> DesiredConfig:
        """Persist a device's desired config, auto-bumping ``config_version``.

        The operator never has to hand-manage the version counter: each write is
        the previous version + 1, which is precisely the signal the edge uses to
        decide whether a pulled config is new and worth applying.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM desired_config WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            prev_version = 0
            if row:
                prev_version = DesiredConfig.model_validate_json(row[0]).config_version
            config = config.model_copy(update={
                "config_version": prev_version + 1,
                "updated_at": datetime.now(timezone.utc),
            })
            self._conn.execute(
                """
                INSERT INTO desired_config (device_id, tenant_id, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    payload   = excluded.payload
                """,
                (device_id, tenant_id, config.model_dump_json()),
            )
            self._conn.commit()
        return config

    def all_configs(self, tenant_id: str) -> dict[str, DesiredConfig]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id, payload FROM desired_config WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        return {r[0]: DesiredConfig.model_validate_json(r[1]) for r in rows}

    # -- release ------------------------------------------------------------ #

    def get_release(self, device_id: str) -> Optional[DesiredRelease]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM desired_release WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return DesiredRelease.model_validate_json(row[0]) if row else None

    def set_release(self, device_id: str, tenant_id: str, release: DesiredRelease) -> DesiredRelease:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO desired_release (device_id, tenant_id, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    payload   = excluded.payload
                """,
                (device_id, tenant_id, release.model_dump_json()),
            )
            self._conn.commit()
        return release

    def device_ids(self, tenant_id: str) -> list[str]:
        """Union of devices known from either config or release rows."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT device_id FROM desired_config WHERE tenant_id = ?
                UNION
                SELECT device_id FROM desired_release WHERE tenant_id = ?
                """,
                (tenant_id, tenant_id),
            ).fetchall()
        return sorted(r[0] for r in rows)


# --------------------------------------------------------------------------- #
# Injectable seams (set once at wire-up; overridable in tests)
# --------------------------------------------------------------------------- #

_store: FleetStore | None = None
_heartbeat_accessor: HeartbeatAccessor | None = None


def configure(
    *,
    store: FleetStore | None = None,
    heartbeat_accessor: HeartbeatAccessor | None = None,
) -> None:
    """Inject the desired-state store and/or the heartbeat accessor.

    Tests call this with a temp ``FleetStore`` and a fake accessor. Production wiring
    can leave both defaulted (see :func:`_get_store` / :func:`_get_heartbeats`).
    """
    global _store, _heartbeat_accessor
    if store is not None:
        _store = store
    if heartbeat_accessor is not None:
        _heartbeat_accessor = heartbeat_accessor


def _get_store() -> FleetStore:
    global _store
    if _store is None:
        _store = FleetStore(os.environ.get("CAMAI_FLEET_DB", "camai-fleet.db"))
    return _store


def _get_heartbeats(tenant_id: str) -> list[dict]:
    if _heartbeat_accessor is not None:
        return _heartbeat_accessor(tenant_id)
    # Lazy default: read heartbeats from the main event store. Imported lazily so
    # importing this router never forces a store backend to initialize.
    from app.store_factory import get_store
    return get_store().device_health(tenant_id)


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #

class RolloutRequest(BaseModel):
    """Start a canary rollout of ``release`` across a tenant's fleet.

    A fraction (or explicit count) of devices, chosen deterministically, are pinned
    to ``release`` on the canary channel; everyone else is pinned to
    ``baseline`` (or left as-is if no baseline given). This is the API face of
    :func:`app.fleet.select_canary`.
    """

    release: DesiredRelease
    fraction: Optional[float] = Field(default=None, ge=0, le=1)
    count: Optional[int] = Field(default=None, ge=0)
    baseline: Optional[DesiredRelease] = None
    devices: Optional[list[str]] = Field(
        default=None,
        description="Explicit device set to roll across; defaults to devices known "
        "to the fleet store plus any that have sent a heartbeat for this tenant.",
    )


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #

router = APIRouter(tags=["fleet"])


@router.get("/v1/devices/{device_id}/config", response_model=DesiredConfig)
def get_device_config(device_id: str) -> DesiredConfig:
    """Edge pulls its desired config. Returns an empty (version 0) config when the
    operator has set nothing, so a fresh device gets a well-formed no-op rather than
    a 404 it would have to special-case."""
    return _get_store().get_config(device_id) or DesiredConfig()


@router.put("/v1/devices/{device_id}/config", response_model=DesiredConfig)
def put_device_config(
    device_id: str,
    config: DesiredConfig,
    tenant_id: str,
) -> DesiredConfig:
    """Operator sets a device's desired config. ``config_version`` is assigned by
    the server (see :meth:`FleetStore.set_config`), so any value the caller sends is
    ignored — the returned object carries the authoritative version."""
    return _get_store().set_config(device_id, tenant_id, config)


@router.get("/v1/devices/{device_id}/release", response_model=DesiredRelease)
def get_device_release(device_id: str) -> DesiredRelease:
    """Edge pulls its pinned agent/model version. 404 when nothing is pinned yet, so
    the edge keeps running whatever it currently has instead of downgrading."""
    release = _get_store().get_release(device_id)
    if release is None:
        raise HTTPException(status_code=404, detail="no release pinned for device")
    return release


@router.put("/v1/devices/{device_id}/release", response_model=DesiredRelease)
def put_device_release(
    device_id: str,
    release: DesiredRelease,
    tenant_id: str,
) -> DesiredRelease:
    """Operator pins a device to a specific agent/model version."""
    return _get_store().set_release(device_id, tenant_id, release)


@router.put("/v1/tenants/{tenant_id}/rollout")
def start_rollout(tenant_id: str, req: RolloutRequest) -> dict:
    """Canary-roll ``release`` across the tenant's fleet.

    Deterministically selects the canary subset and pins each device accordingly,
    so re-running the same rollout is idempotent and never reshuffles which boxes
    are on the new build.
    """
    store = _get_store()

    # Device universe: whatever the fleet store knows, plus anything that has ever
    # sent a heartbeat (so a brand-new fleet can still be rolled).
    devices = set(req.devices or [])
    if not devices:
        devices.update(store.device_ids(tenant_id))
        devices.update(
            hb.get("device_id")
            for hb in _get_heartbeats(tenant_id)
            if hb.get("device_id")
        )
    device_list = sorted(devices)
    if not device_list:
        raise HTTPException(status_code=400, detail="no devices to roll out to")

    if req.fraction is None and req.count is None:
        raise HTTPException(status_code=400, detail="provide fraction or count")

    canary = set(select_canary(
        device_list,
        fraction=req.fraction,
        count=req.count,
        salt=f"{tenant_id}:{req.release.agent_version}",
    ))

    canary_release = req.release.model_copy(update={"channel": ReleaseChannel.canary})
    baseline = req.baseline

    assignments: dict[str, str] = {}
    for device_id in device_list:
        if device_id in canary:
            store.set_release(device_id, tenant_id, canary_release)
            assignments[device_id] = "canary"
        elif baseline is not None:
            store.set_release(device_id, tenant_id, baseline)
            assignments[device_id] = "baseline"
        else:
            assignments[device_id] = "unchanged"

    return {
        "tenant_id": tenant_id,
        "agent_version": req.release.agent_version,
        "canary": sorted(canary),
        "canary_count": len(canary),
        "total_devices": len(device_list),
        "assignments": assignments,
    }


@router.get("/v1/tenants/{tenant_id}/alerts")
def tenant_alerts(
    tenant_id: str,
    stale_after_seconds: float = 120.0,
    offline_after_seconds: float = 600.0,
    min_fps_ratio: float = 0.5,
    default_expected_fps: Optional[float] = None,
) -> dict:
    """Live fleet alerts for a tenant, derived from heartbeats + desired config.

    Read-side and cheap: it re-derives alerts on each poll from the heartbeats
    already in the store and the per-camera expected FPS in each device's desired
    config, so there is no separate alert table to keep consistent.
    """
    heartbeats = _get_heartbeats(tenant_id)
    configs = _get_store().all_configs(tenant_id)
    alerts = compute_alerts(
        heartbeats,
        configs=configs,
        default_expected_fps=default_expected_fps,
        stale_after_seconds=stale_after_seconds,
        offline_after_seconds=offline_after_seconds,
        min_fps_ratio=min_fps_ratio,
    )
    return {
        "tenant_id": tenant_id,
        "count": len(alerts),
        "alerts": [a.model_dump(mode="json") for a in alerts],
    }
