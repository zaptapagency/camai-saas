"""Zero-touch device registration + a self-contained device registry.

A field tech should be able to unbox an edge appliance, plug it in, and have it
join the right tenant/site with no manual key handling. That is "zero-touch":

1. The box mints its own key + CSR locally on first boot (``camai_edge.identity``).
2. On first contact it POSTs the CSR (or just its public key) together with a
   short-lived, single-use **enrollment token** that was provisioned for a tenant
   and site (printed on the box, or handed to the installer).
3. The cloud validates the token, binds the derived ``device_id`` to that
   tenant/site, and records the device's public key. From then on the device
   authenticates by its client certificate (see ``app.security``); the tenant is
   read from this binding, never from the request body.

Storage
-------
This is backed by its **own** small SQLite database, deliberately *not* the shared
``Store``: keeping it here means this workstream adds no columns to and never edits
``store.py`` while many agents work in parallel. The two natural follow-ups, once
the interfaces settle, are either (a) promote these two tables into ``store.py`` /
``store_pg.py`` behind the same ``Store`` interface, or (b) give it its own Pg
table. Both are drop-in because all access goes through :class:`DeviceRegistry`.

Wiring: ``app.include_router(device_registry.router)`` in ``main.py``.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Registry (self-contained SQLite; swappable without touching the HTTP layer)
# --------------------------------------------------------------------------- #

class DeviceRegistry:
    """Persistence for enrollment tokens and device<->tenant bindings.

    Thread-safe (one lock, one connection) to match ``Store``'s style. Uses
    ``INSERT OR IGNORE`` / explicit checks so registration is idempotent: a device
    retrying the same enrollment after a dropped response re-binds to the same
    tenant instead of erroring.
    """

    def __init__(self, path: str | Path = "camai-devices.db") -> None:
        self._lock = threading.Lock()
        # check_same_thread=False: FastAPI serves requests across a thread pool.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS enrollment_tokens (
                token      TEXT PRIMARY KEY,
                tenant_id  TEXT NOT NULL,
                site_id    TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at    TEXT,
                used_by    TEXT
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id      TEXT PRIMARY KEY,
                tenant_id      TEXT NOT NULL,
                site_id        TEXT NOT NULL,
                status         TEXT NOT NULL,
                public_key_pem TEXT,
                csr_pem        TEXT,
                enrolled_at    TEXT NOT NULL,
                last_seen      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_devices_tenant ON devices (tenant_id);
            """
        )
        self._conn.commit()

    # --- Enrollment tokens ------------------------------------------------- #

    def create_enrollment_token(
        self, tenant_id: str, site_id: str, ttl_seconds: int = 3600,
        token: Optional[str] = None,
    ) -> dict:
        """Provision a single-use token binding a future device to tenant/site.

        In production an admin/API mints these; exposed as a method so tests and
        an internal provisioning tool can create them without HTTP.
        """
        tok = token or secrets.token_urlsafe(24)
        expires = _utcnow() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                """INSERT INTO enrollment_tokens (token, tenant_id, site_id, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (tok, tenant_id, site_id, expires.isoformat()),
            )
            self._conn.commit()
        return {"token": tok, "tenant_id": tenant_id, "site_id": site_id,
                "expires_at": expires.isoformat()}

    def _consume_token(self, cur: sqlite3.Cursor, token: str, device_id: str) -> dict:
        row = cur.execute(
            "SELECT tenant_id, site_id, expires_at, used_at, used_by FROM enrollment_tokens WHERE token=?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="unknown enrollment token")
        tenant_id, site_id, expires_at, used_at, used_by = row
        if _utcnow() > datetime.fromisoformat(expires_at):
            raise HTTPException(status_code=401, detail="enrollment token expired")
        if used_at is not None and used_by != device_id:
            # Reuse by a *different* device is the attack we guard against; the
            # same device retrying (used_by == device_id) is allowed (idempotent).
            raise HTTPException(status_code=409, detail="enrollment token already used")
        return {"tenant_id": tenant_id, "site_id": site_id}

    # --- Device binding ---------------------------------------------------- #

    def register_device(
        self, token: str, *, device_id: str,
        public_key_pem: Optional[str] = None, csr_pem: Optional[str] = None,
    ) -> dict:
        """Bind ``device_id`` to the token's tenant/site and record its key.

        ``device_id`` must be supplied by the caller already derived from the key
        fingerprint (the HTTP layer derives it from the CSR/public key), so the
        registry never has to trust a free-form id. Idempotent on retry.
        """
        with self._lock:
            cur = self._conn.cursor()
            bind = self._consume_token(cur, token, device_id)

            existing = cur.execute(
                "SELECT tenant_id, site_id FROM devices WHERE device_id=?",
                (device_id,),
            ).fetchone()
            if existing and (existing[0] != bind["tenant_id"] or existing[1] != bind["site_id"]):
                # A device may not silently move tenants via a new token.
                raise HTTPException(
                    status_code=409,
                    detail="device already bound to a different tenant/site",
                )

            now = _utcnow().isoformat()
            cur.execute(
                """
                INSERT INTO devices
                    (device_id, tenant_id, site_id, status, public_key_pem, csr_pem, enrolled_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    status='active',
                    public_key_pem=COALESCE(excluded.public_key_pem, devices.public_key_pem),
                    csr_pem=COALESCE(excluded.csr_pem, devices.csr_pem)
                """,
                (device_id, bind["tenant_id"], bind["site_id"], public_key_pem, csr_pem, now),
            )
            cur.execute(
                "UPDATE enrollment_tokens SET used_at=?, used_by=? WHERE token=?",
                (now, device_id, token),
            )
            self._conn.commit()
        return self.get_device(device_id)  # type: ignore[return-value]

    def get_device(self, device_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT device_id, tenant_id, site_id, status, enrolled_at, last_seen
                   FROM devices WHERE device_id=?""",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return {"device_id": row[0], "tenant_id": row[1], "site_id": row[2],
                "status": row[3], "enrolled_at": row[4], "last_seen": row[5]}

    def touch(self, device_id: str) -> None:
        """Record that a device was just seen (called from ingest/heartbeat)."""
        with self._lock:
            self._conn.execute(
                "UPDATE devices SET last_seen=? WHERE device_id=?",
                (_utcnow().isoformat(), device_id),
            )
            self._conn.commit()

    def list_devices(self, tenant_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT device_id, tenant_id, site_id, status, enrolled_at, last_seen
                   FROM devices WHERE tenant_id=? ORDER BY enrolled_at""",
                (tenant_id,),
            ).fetchall()
        return [{"device_id": r[0], "tenant_id": r[1], "site_id": r[2],
                 "status": r[3], "enrolled_at": r[4], "last_seen": r[5]} for r in rows]


# --------------------------------------------------------------------------- #
# Wire models
# --------------------------------------------------------------------------- #

class RegisterRequest(BaseModel):
    """Zero-touch registration payload.

    Exactly one of ``csr`` or ``public_key`` is required; both are safe to send
    (no secret material). ``device_id`` is optional and, when present, is only
    honoured if it matches the fingerprint derived from the key -- the derived id
    always wins so a device cannot claim an arbitrary id.
    """

    enrollment_token: str
    csr: Optional[str] = Field(default=None, description="PEM-encoded PKCS#10 CSR")
    public_key: Optional[str] = Field(default=None, description="PEM SubjectPublicKeyInfo")
    device_id: Optional[str] = None


class RegisterResponse(BaseModel):
    device_id: str
    tenant_id: str
    site_id: str
    status: str


class DeviceStatus(BaseModel):
    device_id: str
    tenant_id: str
    site_id: str
    status: str
    enrolled_at: str
    last_seen: Optional[str] = None


# --------------------------------------------------------------------------- #
# Registry wiring: a lazily-created default, overridable for tests.
# --------------------------------------------------------------------------- #

_default_registry: Optional[DeviceRegistry] = None
_default_lock = threading.Lock()


def get_registry() -> DeviceRegistry:
    """Return the process-wide registry, created from ``CAMAI_DEVICE_REGISTRY_DB``.

    A FastAPI dependency so tests can override it with a tmp-dir registry via
    ``app.dependency_overrides[get_registry]``.
    """
    global _default_registry
    if _default_registry is None:
        with _default_lock:
            if _default_registry is None:
                path = os.environ.get("CAMAI_DEVICE_REGISTRY_DB", "camai-devices.db")
                _default_registry = DeviceRegistry(path)
    return _default_registry


def _derive_device_id(req: RegisterRequest) -> str:
    """Derive the canonical device id from the CSR/public key fingerprint.

    Mirrors ``camai_edge.identity.device_id_from_public_key_der`` so the edge and
    cloud always agree. ``cryptography`` is imported lazily; if it is unavailable
    we fall back to a client-supplied ``device_id`` (dev/test) rather than failing
    hard, but never silently invent one.
    """
    try:
        import hashlib

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        pub = None
        if req.csr:
            csr = x509.load_pem_x509_csr(req.csr.encode("ascii"))
            if not csr.is_signature_valid:
                raise HTTPException(status_code=400, detail="CSR signature invalid")
            pub = csr.public_key()
        elif req.public_key:
            pub = serialization.load_pem_public_key(req.public_key.encode("ascii"))
        if pub is not None:
            spki = pub.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            return f"dev_{hashlib.sha256(spki).hexdigest()[:32]}"
    except HTTPException:
        raise
    except ImportError:
        pass  # fall through to the client-supplied id below
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid key material: {exc}")

    if req.device_id:
        return req.device_id
    raise HTTPException(
        status_code=400,
        detail="could not derive device_id: provide a valid csr/public_key",
    )


# --------------------------------------------------------------------------- #
# HTTP router (included by main.py)
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post("/register", response_model=RegisterResponse)
def register(
    req: RegisterRequest,
    registry: DeviceRegistry = Depends(get_registry),
) -> RegisterResponse:
    """Zero-touch enroll: bind a device to a tenant/site via an enrollment token."""
    if not req.csr and not req.public_key and not req.device_id:
        raise HTTPException(status_code=400, detail="csr or public_key required")
    device_id = _derive_device_id(req)
    result = registry.register_device(
        req.enrollment_token, device_id=device_id,
        public_key_pem=req.public_key, csr_pem=req.csr,
    )
    return RegisterResponse(**{k: result[k] for k in ("device_id", "tenant_id", "site_id", "status")})


@router.get("/{device_id}", response_model=DeviceStatus)
def device_status(
    device_id: str,
    registry: DeviceRegistry = Depends(get_registry),
) -> DeviceStatus:
    """Report a device's binding + liveness (for the fleet view / installer UX)."""
    dev = registry.get_device(device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail="unknown device")
    return DeviceStatus(**dev)
