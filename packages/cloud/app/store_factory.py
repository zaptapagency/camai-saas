"""Select the storage backend at runtime.

``CAMAI_STORE=sqlite`` (default) uses the embedded dev store; ``postgres`` uses
TimescaleDB via ``CAMAI_PG_DSN``. Both implement the same interface, so the API,
dashboard, and billing code are backend-agnostic.
"""

from __future__ import annotations

import os


def get_store():
    backend = os.environ.get("CAMAI_STORE", "sqlite").lower()
    if backend == "postgres":
        from app.store_pg import PgStore
        dsn = os.environ.get("CAMAI_PG_DSN")
        if not dsn:
            raise RuntimeError("CAMAI_STORE=postgres requires CAMAI_PG_DSN")
        return PgStore(dsn)
    from app.store import Store
    return Store(os.environ.get("CAMAI_DB", "camai-cloud.db"))
