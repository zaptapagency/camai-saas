"""Time-boxed demo-session store — the backend for the shareable 3-hour demo.

A prospect clicks "Launch live demo"; we mint an opaque token that grants read
access to the shared ``demo-master`` tenant for a fixed window (default 3 hours,
``CAMAI_DEMO_HOURS``). The dashboard carries the token in a ``?demo=`` link, polls
:func:`session` to render a countdown, and locks the view to ``demo-master``. When
the window closes the token is reported expired and the UI shows a "demo ended"
screen — no standing account, no cleanup to chase.

Design mirrors :mod:`app.snapshots` / :mod:`app.audit`: a standalone, thread-safe
SQLite store (``CAMAI_DEMO_DB``, in-memory by default) independent of the main
event store, so issuing/validating a demo link never touches tenant data.

Tokens are read-only session handles, not credentials: they authorize viewing the
demo tenant's aggregates for the window, nothing else. The demo tenant itself holds
only anonymous counts/events like any other tenant.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DEMO_DB_ENV = "CAMAI_DEMO_DB"
DEMO_HOURS_ENV = "CAMAI_DEMO_HOURS"
DEMO_TENANT = "demo-master"

_DEFAULT_HOURS = 3.0


def _hours() -> float:
    try:
        return float(os.environ.get(DEMO_HOURS_ENV, _DEFAULT_HOURS))
    except ValueError:
        return _DEFAULT_HOURS


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoSessionStore:
    """Thread-safe store of time-boxed demo sessions, backed by its own SQLite db."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        db = str(db_path) if db_path is not None else (os.environ.get(DEMO_DB_ENV) or ":memory:")
        self._conn = sqlite3.connect(db, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_sessions (
                token      TEXT PRIMARY KEY,
                tenant_id  TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def start(self, hours: float | None = None) -> dict:
        """Mint a fresh demo session and return its public view."""
        token = secrets.token_urlsafe(24)
        created = _now()
        expires = created + timedelta(hours=hours if hours is not None else _hours())
        with self._lock:
            self._conn.execute(
                "INSERT INTO demo_sessions (token, tenant_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, DEMO_TENANT, created.isoformat(), expires.isoformat()),
            )
            self._conn.commit()
        return self._view(token, DEMO_TENANT, created, expires)

    def session(self, token: str) -> Optional[dict]:
        """Return the session's public view (incl. expiry state), or ``None`` if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT token, tenant_id, created_at, expires_at FROM demo_sessions WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        return self._view(row[0], row[1],
                          datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]))

    def purge_expired(self) -> int:
        """Delete sessions whose window closed. Returns how many were removed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM demo_sessions WHERE expires_at < ?", (_now().isoformat(),))
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _view(token: str, tenant: str, created: datetime, expires: datetime) -> dict:
        remaining = (expires - _now()).total_seconds()
        return {
            "token": token,
            "tenant_id": tenant,
            "created_at": created.isoformat(),
            "expires_at": expires.isoformat(),
            "seconds_remaining": max(0, int(remaining)),
            "expired": remaining <= 0,
        }


# --------------------------------------------------------------------------- #
# Module-level default instance + a test seam.
# --------------------------------------------------------------------------- #

_default: Optional[DemoSessionStore] = None


def get_store() -> DemoSessionStore:
    global _default
    if _default is None:
        _default = DemoSessionStore()
    return _default


def configure(db_path: str | Path | None = None) -> DemoSessionStore:
    """Replace the default instance (tests point it at a fresh, isolated db)."""
    global _default
    _default = DemoSessionStore(db_path)
    return _default


def start(hours: float | None = None) -> dict:
    return get_store().start(hours)


def session(token: str) -> Optional[dict]:
    return get_store().session(token)


def purge_expired() -> int:
    return get_store().purge_expired()
