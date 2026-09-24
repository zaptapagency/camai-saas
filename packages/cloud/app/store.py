"""Minimal event store for the ingest slice.

This is a stand-in for the real control-plane database (Postgres + TimescaleDB).
It uses SQLite so the ingest API runs with zero setup, while keeping the two
properties that matter for correctness now and carry over to Postgres later:

* **idempotency** — ``event_id`` is the primary key, so a retried batch (after an
  edge connectivity drop) is deduped rather than double-counted.
* **append-only** — events are only inserted, never updated in place.

Swap this module for a Timescale-backed repository without touching the API.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from camai_schema import Event, Heartbeat


class Store:
    def __init__(self, path: str | Path = "camai-cloud.db") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id  TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                site_id   TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                ts        TEXT NOT NULL,
                type      TEXT NOT NULL,
                payload   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_tenant_ts ON events (tenant_id, ts);

            CREATE TABLE IF NOT EXISTS heartbeats (
                device_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                ts        TEXT NOT NULL,
                payload   TEXT NOT NULL
            );

            -- Maps a tenant to its Stripe billing objects. Metered usage
            -- (active cameras) is reported against stripe_subscription_item_id.
            CREATE TABLE IF NOT EXISTS billing_accounts (
                tenant_id                  TEXT PRIMARY KEY,
                stripe_customer_id         TEXT,
                stripe_subscription_item_id TEXT,
                plan                       TEXT
            );
            """
        )
        self._conn.commit()

    def insert_events(self, events: list[Event]) -> tuple[int, int]:
        """Insert events idempotently. Returns (accepted, duplicates)."""
        accepted = 0
        duplicates = 0
        with self._lock:
            cur = self._conn.cursor()
            for e in events:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO events
                        (event_id, tenant_id, site_id, camera_id, ts, type, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (e.event_id, e.tenant_id, e.site_id, e.camera_id,
                     e.ts.isoformat(), _as_str(e.type), e.model_dump_json()),
                )
                if cur.rowcount == 1:
                    accepted += 1
                else:
                    duplicates += 1
            self._conn.commit()
        return accepted, duplicates

    def upsert_heartbeat(self, hb: Heartbeat) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO heartbeats (device_id, tenant_id, ts, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    ts = excluded.ts,
                    payload = excluded.payload
                """,
                (hb.device_id, hb.tenant_id, hb.ts.isoformat(), hb.model_dump_json()),
            )
            self._conn.commit()

    def recent_events(self, tenant_id: str, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM events WHERE tenant_id = ? ORDER BY ts DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def device_health(self, tenant_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM heartbeats WHERE tenant_id = ? ORDER BY ts DESC",
                (tenant_id,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def summary(self, tenant_id: str) -> dict:
        """Aggregate the raw event stream into the tiles the dashboard shows.

        Computed on read for the pilot. In production these become TimescaleDB
        continuous aggregates so the query stays cheap at scale — the shape here
        is the target contract for that.
        """
        with self._lock:
            cur = self._conn.cursor()

            # Retail totals + running occupancy.
            entries = _scalar(cur.execute(
                "SELECT COUNT(*) FROM events WHERE tenant_id=? AND type='entry'",
                (tenant_id,)))
            exits = _scalar(cur.execute(
                "SELECT COUNT(*) FROM events WHERE tenant_id=? AND type='exit'",
                (tenant_id,)))
            ppe_violations = _scalar(cur.execute(
                "SELECT COUNT(*) FROM events WHERE tenant_id=? AND type='ppe_violation'",
                (tenant_id,)))
            vehicle_crossings = _scalar(cur.execute(
                "SELECT COUNT(*) FROM events WHERE tenant_id=? AND type='vehicle_crossing'",
                (tenant_id,)))

            # Latest occupancy sample per (camera, zone). SQLite returns the row
            # matching MAX(ts) for the other selected columns.
            occ_rows = cur.execute(
                """
                SELECT camera_id,
                       json_extract(payload, '$.zone_id')  AS zone_id,
                       json_extract(payload, '$.count')    AS count,
                       MAX(ts)                             AS ts
                FROM events
                WHERE tenant_id=? AND type='occupancy_sample'
                GROUP BY camera_id, zone_id
                """,
                (tenant_id,),
            ).fetchall()

            # Latest parking state per space.
            park_rows = cur.execute(
                """
                SELECT camera_id,
                       json_extract(payload, '$.zone_id') AS zone_id,
                       type,
                       MAX(ts)                            AS ts
                FROM events
                WHERE tenant_id=? AND type IN ('vehicle_parked','vehicle_left')
                GROUP BY camera_id, zone_id
                """,
                (tenant_id,),
            ).fetchall()

            # Recent queue wait times (dwell events) for the average-wait tile.
            wait_rows = cur.execute(
                """
                SELECT json_extract(payload, '$.dwell_seconds')
                FROM events
                WHERE tenant_id=? AND type='dwell'
                ORDER BY ts DESC LIMIT 200
                """,
                (tenant_id,),
            ).fetchall()

            # Latest headcount per staffing station (mode='staffing') for coverage.
            staffing_rows = cur.execute(
                """
                SELECT camera_id,
                       json_extract(payload, '$.zone_id') AS zone_id,
                       json_extract(payload, '$.count')   AS count,
                       MAX(ts)                            AS ts
                FROM events
                WHERE tenant_id=? AND type='occupancy_sample'
                      AND json_extract(payload, '$.mode')='staffing'
                GROUP BY camera_id, zone_id
                """,
                (tenant_id,),
            ).fetchall()

        occupancy = [
            {"camera_id": r[0], "zone_id": r[1], "count": r[2], "ts": r[3]}
            for r in occ_rows
        ]
        parking = [
            {"camera_id": r[0], "zone_id": r[1],
             "state": "parked" if r[2] == "vehicle_parked" else "free", "ts": r[3]}
            for r in park_rows
        ]
        spaces_occupied = sum(1 for p in parking if p["state"] == "parked")

        waits = [r[0] for r in wait_rows if r[0] is not None]
        avg_wait = round(sum(waits) / len(waits), 1) if waits else None

        stations_total = len(staffing_rows)
        stations_unstaffed = sum(1 for r in staffing_rows if (r[2] or 0) == 0)

        return {
            "tenant_id": tenant_id,
            "totals": {
                "entries": entries,
                "exits": exits,
                "retail_occupancy": max(0, entries - exits),
                "parking_spaces_occupied": spaces_occupied,
                "avg_wait_seconds": avg_wait,
                "wait_samples": len(waits),
                "ppe_violations": ppe_violations,
                "vehicle_crossings": vehicle_crossings,
                "stations_total": stations_total,
                "stations_unstaffed": stations_unstaffed,
            },
            "occupancy": occupancy,
            "parking": parking,
            "devices": self.device_health(tenant_id),
            "recent": self.recent_events(tenant_id, limit=25),
        }

    # --- Billing / metering ------------------------------------------------- #

    def active_camera_ids(self, tenant_id: str, start_iso: str, end_iso: str) -> list[str]:
        """Cameras that reported at least one event in [start, end).

        This is the billed unit: a camera is "active" for a period if its edge
        agent emitted any event during it. ISO-8601 UTC strings compare correctly
        lexicographically because every ts is written in the same normalized form.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT camera_id FROM events
                WHERE tenant_id = ? AND ts >= ? AND ts < ?
                ORDER BY camera_id
                """,
                (tenant_id, start_iso, end_iso),
            ).fetchall()
        return [r[0] for r in rows]

    def get_billing_account(self, tenant_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT tenant_id, stripe_customer_id, stripe_subscription_item_id, plan
                   FROM billing_accounts WHERE tenant_id = ?""",
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        return {"tenant_id": row[0], "stripe_customer_id": row[1],
                "stripe_subscription_item_id": row[2], "plan": row[3]}

    def upsert_billing_account(self, tenant_id: str, stripe_customer_id: str,
                               stripe_subscription_item_id: str, plan: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO billing_accounts
                    (tenant_id, stripe_customer_id, stripe_subscription_item_id, plan)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    stripe_customer_id = excluded.stripe_customer_id,
                    stripe_subscription_item_id = excluded.stripe_subscription_item_id,
                    plan = excluded.plan
                """,
                (tenant_id, stripe_customer_id, stripe_subscription_item_id, plan),
            )
            self._conn.commit()

    def all_billing_accounts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT tenant_id, stripe_customer_id, stripe_subscription_item_id, plan
                   FROM billing_accounts"""
            ).fetchall()
        return [{"tenant_id": r[0], "stripe_customer_id": r[1],
                 "stripe_subscription_item_id": r[2], "plan": r[3]} for r in rows]


def _as_str(value) -> str:
    # ``use_enum_values`` means Event.type is already a str, but be defensive.
    return value if isinstance(value, str) else getattr(value, "value", str(value))


def _scalar(cursor) -> int:
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0
