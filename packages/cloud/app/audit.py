"""Append-only audit log — the second half of the enterprise-trust gate.

Every privileged mutation (role-gated by :mod:`app.rbac`) is recorded here as an
immutable row: *who* did *what*, to *which* tenant/resource, and *when*. This is
what lets a customer's security team answer "who changed our billing wiring?" or
"who pushed that config?" after the fact, and it is a hard requirement in most
enterprise security questionnaires (SOC 2 CC-series, ISO 27001 A.12.4).

Design
------
* **Append-only.** The table is only ever ``INSERT``-ed into and ``SELECT``-ed
  from — there is no update or delete path in this module, by construction.
* **Standalone.** It uses its own tiny SQLite database (``CAMAI_AUDIT_DB``, or an
  in-memory db by default) and deliberately does *not* depend on ``app.store`` /
  Postgres, so audit logging keeps working even if the main store is swapped,
  migrating, or down — the integrity of the audit trail must not be coupled to
  the availability of the thing it audits.
* **Thread-safe.** A single shared connection guarded by a lock, matching the
  pattern already used by ``app.store.Store`` and ``fleet_router.FleetStore``.

The row id is a monotonic autoincrement, so ``list_entries`` returns newest-first
by id even when two rows share a timestamp.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Env var pointing at the audit db file. Unset -> an in-memory db (the default
# instance keeps one connection, so in-memory rows persist for the process life).
AUDIT_DB_ENV = "CAMAI_AUDIT_DB"


class AuditLog:
    """A thread-safe, append-only audit trail backed by its own SQLite db."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        db = str(path) if path is not None else (os.environ.get(AUDIT_DB_ENV) or ":memory:")
        # check_same_thread=False: one shared connection serialized by _lock, so
        # FastAPI's threadpool workers can all record through it.
        self._conn = sqlite3.connect(db, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                actor     TEXT NOT NULL,
                action    TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                resource  TEXT NOT NULL DEFAULT '',
                meta      TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_tenant_id ON audit_log (tenant_id, id)"
        )
        self._conn.commit()

    def record(
        self,
        actor: str,
        action: str,
        tenant_id: str,
        resource: str = "",
        meta: Optional[dict] = None,
    ) -> dict:
        """Append one audit row with a UTC timestamp. Returns the stored entry."""
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(meta or {}, default=str, sort_keys=True)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO audit_log (ts, actor, action, tenant_id, resource, meta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, actor, action, tenant_id, resource, meta_json),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        return {
            "id": row_id,
            "ts": ts,
            "actor": actor,
            "action": action,
            "tenant_id": tenant_id,
            "resource": resource,
            "meta": meta or {},
        }

    def list_entries(self, tenant_id: str, limit: int = 100) -> list[dict]:
        """Return a tenant's audit entries, newest first (by insertion order)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, ts, actor, action, tenant_id, resource, meta
                FROM audit_log
                WHERE tenant_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "actor": r[2],
                "action": r[3],
                "tenant_id": r[4],
                "resource": r[5],
                "meta": _loads(r[6]),
            }
            for r in rows
        ]


def _loads(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


# --------------------------------------------------------------------------- #
# Module-level default instance + a test seam to point it at a fresh db.
# --------------------------------------------------------------------------- #

_default: Optional[AuditLog] = None


def get_audit() -> AuditLog:
    """The process-wide default audit log, created lazily on first use."""
    global _default
    if _default is None:
        _default = AuditLog()
    return _default


def configure(path: str | Path | None = None) -> AuditLog:
    """Replace the default instance (tests point it at a fresh, isolated db)."""
    global _default
    _default = AuditLog(path)
    return _default


def record(
    actor: str,
    action: str,
    tenant_id: str,
    resource: str = "",
    meta: Optional[dict] = None,
) -> dict:
    """Append to the default audit log. See :meth:`AuditLog.record`."""
    return get_audit().record(actor, action, tenant_id, resource, meta)


def list_entries(tenant_id: str, limit: int = 100) -> list[dict]:
    """Read from the default audit log. See :meth:`AuditLog.list_entries`."""
    return get_audit().list_entries(tenant_id, limit)
