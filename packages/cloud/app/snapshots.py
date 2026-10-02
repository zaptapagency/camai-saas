"""Snapshot + label + accuracy store — the "accuracy flywheel" hub.

This is the backend that turns live inference into a continuously improving
product. The edge agent periodically pushes a *snapshot* — one JPEG frame plus
the model's predicted count for a camera — and the dashboard lets a human label
it with the true count. Each label becomes a ``(predicted, actual)`` sample, and
from those samples we compute per-tenant / per-mode accuracy. That measured error
is the flywheel: it tells us which cameras and which modes to retrain, and gives
the customer an honest, auditable accuracy number instead of a marketing claim.

Design
------
* **Standalone.** Like :mod:`app.audit`, this keeps its own tiny SQLite database
  (``CAMAI_SNAPSHOT_DB``, in-memory by default) and writes image bytes to disk
  under ``CAMAI_SNAPSHOT_DIR`` — it does *not* depend on ``app.store`` / Postgres.
  The labeling/accuracy loop must keep working even if the main event store is
  swapped, migrating, or down.
* **Latest-only snapshots.** We keep exactly one snapshot per ``(tenant, camera)``
  (an upsert) — the review UI only ever needs the most recent frame. Labels, by
  contrast, are append-only: every human judgement is a durable training sample.
* **Thread-safe.** A single shared connection guarded by a lock, matching the
  pattern used by ``app.store.Store``, ``fleet_router.FleetStore`` and
  ``app.audit.AuditLog``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Env var pointing at the snapshot metadata db file. Unset -> an in-memory db
# (the default instance keeps one connection, so in-memory rows persist for the
# process life).
SNAPSHOT_DB_ENV = "CAMAI_SNAPSHOT_DB"
# Env var pointing at the directory that holds the snapshot image bytes.
SNAPSHOT_DIR_ENV = "CAMAI_SNAPSHOT_DIR"

_DEFAULT_IMG_DIR = os.path.join(tempfile.gettempdir(), "camai_snaps")


def _safe_name(value: str) -> str:
    """Make a tenant/camera id safe to embed in a filename.

    Ids are opaque strings; keep only characters that are unambiguous on disk so
    one camera's frame can never escape the image dir or collide across ids.
    """
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)


class SnapshotStore:
    """Thread-safe snapshot/label/accuracy store backed by its own SQLite db."""

    def __init__(self, db_path: str | Path | None = None, img_dir: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        db = str(db_path) if db_path is not None else (os.environ.get(SNAPSHOT_DB_ENV) or ":memory:")
        self._img_dir = Path(str(img_dir) if img_dir is not None else (os.environ.get(SNAPSHOT_DIR_ENV) or _DEFAULT_IMG_DIR))
        self._img_dir.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: one shared connection serialized by _lock, so
        # FastAPI's threadpool workers can all read/write through it.
        self._conn = sqlite3.connect(db, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                tenant_id       TEXT NOT NULL,
                camera_id       TEXT NOT NULL,
                mode            TEXT NOT NULL,
                ts              TEXT NOT NULL,
                predicted_count INTEGER NOT NULL,
                labeled_count   INTEGER,
                PRIMARY KEY (tenant_id, camera_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS labels (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                mode      TEXT NOT NULL,
                predicted INTEGER NOT NULL,
                actual    INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_labels_tenant ON labels (tenant_id, id)"
        )
        self._conn.commit()

    # --- image path helper -------------------------------------------------- #

    def _img_path(self, tenant: str, camera: str) -> Path:
        return self._img_dir / f"{_safe_name(tenant)}__{_safe_name(camera)}.jpg"

    # --- writes ------------------------------------------------------------- #

    def save_snapshot(
        self,
        tenant: str,
        camera: str,
        mode: str,
        ts_iso: str,
        predicted_count: int,
        image_bytes: bytes,
    ) -> dict:
        """Upsert the latest snapshot for ``(tenant, camera)``.

        Writes the image to ``<img_dir>/<tenant>__<camera>.jpg`` and stores the
        metadata row, clearing any prior ``labeled_count`` (a new frame has not
        been labeled yet). Returns the stored metadata.
        """
        with self._lock:
            self._img_path(tenant, camera).write_bytes(image_bytes)
            self._conn.execute(
                """
                INSERT INTO snapshots (tenant_id, camera_id, mode, ts, predicted_count, labeled_count)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(tenant_id, camera_id) DO UPDATE SET
                    mode            = excluded.mode,
                    ts              = excluded.ts,
                    predicted_count = excluded.predicted_count,
                    labeled_count   = NULL
                """,
                (tenant, camera, mode, ts_iso, int(predicted_count)),
            )
            self._conn.commit()
        return {
            "camera_id": camera,
            "mode": mode,
            "ts": ts_iso,
            "predicted_count": int(predicted_count),
            "labeled_count": None,
        }

    def add_label(self, tenant: str, camera: str, actual_count: int) -> Optional[dict]:
        """Record a human ground-truth label for the latest snapshot.

        Reads the latest snapshot's predicted count + mode, appends an immutable
        ``(predicted, actual)`` training sample, and stamps ``labeled_count`` on
        the snapshot. Returns the stored sample, or ``None`` if there is no
        snapshot to label.
        """
        actual = int(actual_count)
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT mode, predicted_count FROM snapshots WHERE tenant_id = ? AND camera_id = ?",
                (tenant, camera),
            ).fetchone()
            if row is None:
                return None
            mode, predicted = row[0], int(row[1])
            cur = self._conn.execute(
                """
                INSERT INTO labels (ts, tenant_id, camera_id, mode, predicted, actual)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, tenant, camera, mode, predicted, actual),
            )
            self._conn.execute(
                "UPDATE snapshots SET labeled_count = ? WHERE tenant_id = ? AND camera_id = ?",
                (actual, tenant, camera),
            )
            self._conn.commit()
            sample_id = cur.lastrowid
        return {
            "id": sample_id,
            "ts": ts,
            "tenant_id": tenant,
            "camera_id": camera,
            "mode": mode,
            "predicted": predicted,
            "actual": actual,
        }

    # --- reads -------------------------------------------------------------- #

    def latest_meta(self, tenant: str, camera: str) -> Optional[dict]:
        """Return the latest snapshot metadata for a camera, or ``None``."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT camera_id, mode, ts, predicted_count, labeled_count
                FROM snapshots WHERE tenant_id = ? AND camera_id = ?
                """,
                (tenant, camera),
            ).fetchone()
        if row is None:
            return None
        return {
            "camera_id": row[0],
            "mode": row[1],
            "ts": row[2],
            "predicted_count": row[3],
            "labeled_count": row[4],
        }

    def latest_image(self, tenant: str, camera: str) -> Optional[bytes]:
        """Return the latest snapshot image bytes for a camera, or ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM snapshots WHERE tenant_id = ? AND camera_id = ?",
                (tenant, camera),
            ).fetchone()
        if row is None:
            return None
        path = self._img_path(tenant, camera)
        if not path.exists():
            return None
        return path.read_bytes()

    def accuracy(self, tenant: str) -> dict:
        """Compute accuracy for a tenant from its labelled samples.

        Overall and per-mode: ``n`` samples, ``mae`` (mean absolute error) and
        ``mean_pct_error`` (mean of per-sample percent error). Per-sample percent
        error is 0 when ``actual == predicted``, else
        ``abs(actual - predicted) / max(actual, 1) * 100``.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT mode, predicted, actual FROM labels WHERE tenant_id = ?",
                (tenant,),
            ).fetchall()

        def _metrics(samples: list[tuple]) -> dict:
            n = len(samples)
            if n == 0:
                return {"n": 0, "mae": 0.0, "mean_pct_error": 0.0}
            abs_errors = [abs(a - p) for (_m, p, a) in samples]
            pct_errors = [
                0.0 if a == p else (abs(a - p) / max(a, 1)) * 100
                for (_m, p, a) in samples
            ]
            return {
                "n": n,
                "mae": round(sum(abs_errors) / n, 2),
                "mean_pct_error": round(sum(pct_errors) / n, 1),
            }

        overall = _metrics(rows)
        modes: dict[str, list[tuple]] = {}
        for r in rows:
            modes.setdefault(r[0], []).append(r)
        per_mode = [
            {"mode": mode, **_metrics(samples)}
            for mode, samples in sorted(modes.items())
        ]
        return {"tenant_id": tenant, "overall": overall, "per_mode": per_mode}


# --------------------------------------------------------------------------- #
# Module-level default instance + a test seam to point it at a fresh db/dir.
# --------------------------------------------------------------------------- #

_default: Optional[SnapshotStore] = None


def get_store() -> SnapshotStore:
    """The process-wide default snapshot store, created lazily on first use."""
    global _default
    if _default is None:
        _default = SnapshotStore()
    return _default


def configure(db_path: str | Path | None = None, img_dir: str | Path | None = None) -> SnapshotStore:
    """Replace the default instance (tests point it at a fresh, isolated db/dir)."""
    global _default
    _default = SnapshotStore(db_path, img_dir)
    return _default


def save_snapshot(
    tenant: str,
    camera: str,
    mode: str,
    ts_iso: str,
    predicted_count: int,
    image_bytes: bytes,
) -> dict:
    return get_store().save_snapshot(tenant, camera, mode, ts_iso, predicted_count, image_bytes)


def latest_meta(tenant: str, camera: str) -> Optional[dict]:
    return get_store().latest_meta(tenant, camera)


def latest_image(tenant: str, camera: str) -> Optional[bytes]:
    return get_store().latest_image(tenant, camera)


def add_label(tenant: str, camera: str, actual_count: int) -> Optional[dict]:
    return get_store().add_label(tenant, camera, actual_count)


def accuracy(tenant: str) -> dict:
    return get_store().accuracy(tenant)
