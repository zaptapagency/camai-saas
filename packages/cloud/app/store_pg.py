"""TimescaleDB-backed store — the production implementation of the store interface.

Mirrors ``app.store.Store`` method-for-method so the API, dashboard, and billing
code are unchanged when ``CAMAI_STORE=postgres``. Requires the schema in
``migrations/`` to be applied first.

Tenant isolation: each tenant-scoped query sets the ``app.tenant_id`` GUC that the
row-level-security policies check (see 001_init.sql). Run the API under a non-owner
DB role so RLS is actually enforced.

psycopg (v3) is imported lazily so the SQLite dev path never needs it installed.
This uses a single locked connection for simplicity; swap in a connection pool
(psycopg_pool) before scaling out.
"""

from __future__ import annotations

import threading

from camai_schema import Event, Heartbeat


class PgStore:
    def __init__(self, dsn: str) -> None:
        import psycopg  # lazy

        self._psycopg = psycopg
        self._lock = threading.Lock()
        self._conn = psycopg.connect(dsn, autocommit=True)

    def _scope(self, cur, tenant_id: str) -> None:
        # Session-level GUC (is_local=false) so it persists across the statements
        # of this call on the shared connection.
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))

    # --- Writes ------------------------------------------------------------- #

    def insert_events(self, events: list[Event]) -> tuple[int, int]:
        if not events:
            return 0, 0
        accepted = duplicates = 0
        with self._lock, self._conn.cursor() as cur:
            self._scope(cur, events[0].tenant_id)
            for e in events:
                cur.execute(
                    """
                    INSERT INTO events (time, event_id, tenant_id, site_id, camera_id,
                        type, mode, zone_id, line_id, object_class, count, delta,
                        dwell_seconds, clip_ref, labels)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id, time) DO NOTHING
                    """,
                    (e.ts, e.event_id, e.tenant_id, e.site_id, e.camera_id,
                     _s(e.type), _s(e.mode), e.zone_id, e.line_id, _s(e.object_class),
                     e.count, e.delta, e.dwell_seconds, e.clip_ref,
                     self._psycopg.types.json.Jsonb(e.labels) if e.labels is not None else None),
                )
                if cur.rowcount == 1:
                    accepted += 1
                else:
                    duplicates += 1
        return accepted, duplicates

    def upsert_heartbeat(self, hb: Heartbeat) -> None:
        with self._lock, self._conn.cursor() as cur:
            self._scope(cur, hb.tenant_id)
            cur.execute(
                """
                INSERT INTO heartbeats (device_id, tenant_id, ts, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    ts = EXCLUDED.ts,
                    payload = EXCLUDED.payload
                """,
                (hb.device_id, hb.tenant_id, hb.ts, self._psycopg.types.json.Jsonb(
                    hb.model_dump(mode="json"))),
            )

    # --- Reads -------------------------------------------------------------- #

    def recent_events(self, tenant_id: str, limit: int = 100) -> list[dict]:
        with self._lock, self._conn.cursor() as cur:
            self._scope(cur, tenant_id)
            cur.execute(
                """
                SELECT time, event_id, camera_id, type, mode, zone_id, line_id,
                       object_class, count, delta, dwell_seconds, labels
                FROM events WHERE tenant_id = %s ORDER BY time DESC LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = cur.fetchall()
        return [
            {"ts": r[0].isoformat(), "event_id": str(r[1]), "camera_id": r[2],
             "type": r[3], "mode": r[4], "zone_id": r[5], "line_id": r[6],
             "object_class": r[7], "count": r[8], "delta": r[9], "dwell_seconds": r[10],
             "labels": r[11]}
            for r in rows
        ]

    def device_health(self, tenant_id: str) -> list[dict]:
        with self._lock, self._conn.cursor() as cur:
            self._scope(cur, tenant_id)
            cur.execute(
                "SELECT payload FROM heartbeats WHERE tenant_id = %s ORDER BY ts DESC",
                (tenant_id,),
            )
            rows = cur.fetchall()
        return [r[0] for r in rows]  # jsonb -> dict automatically

    def active_camera_ids(self, tenant_id: str, start_iso: str, end_iso: str) -> list[str]:
        with self._lock, self._conn.cursor() as cur:
            self._scope(cur, tenant_id)
            cur.execute(
                """
                SELECT DISTINCT camera_id FROM events
                WHERE tenant_id = %s AND time >= %s::timestamptz AND time < %s::timestamptz
                ORDER BY camera_id
                """,
                (tenant_id, start_iso, end_iso),
            )
            return [r[0] for r in cur.fetchall()]

    def summary(self, tenant_id: str) -> dict:
        with self._lock, self._conn.cursor() as cur:
            self._scope(cur, tenant_id)
            cur.execute("SELECT count(*) FROM events WHERE tenant_id=%s AND type='entry'",
                        (tenant_id,))
            entries = _scalar(cur)
            cur.execute("SELECT count(*) FROM events WHERE tenant_id=%s AND type='exit'",
                        (tenant_id,))
            exits = _scalar(cur)
            cur.execute("SELECT count(*) FROM events WHERE tenant_id=%s AND type='ppe_violation'",
                        (tenant_id,))
            ppe_violations = _scalar(cur)
            cur.execute("SELECT count(*) FROM events WHERE tenant_id=%s AND type='vehicle_crossing'",
                        (tenant_id,))
            vehicle_crossings = _scalar(cur)

            cur.execute(
                """
                SELECT DISTINCT ON (camera_id, zone_id)
                       camera_id, zone_id, count, time
                FROM events
                WHERE tenant_id=%s AND type='occupancy_sample'
                ORDER BY camera_id, zone_id, time DESC
                """,
                (tenant_id,),
            )
            occ_rows = cur.fetchall()

            cur.execute(
                """
                SELECT DISTINCT ON (camera_id, zone_id)
                       camera_id, zone_id, type, time
                FROM events
                WHERE tenant_id=%s AND type IN ('vehicle_parked','vehicle_left')
                ORDER BY camera_id, zone_id, time DESC
                """,
                (tenant_id,),
            )
            park_rows = cur.fetchall()

            cur.execute(
                """
                SELECT mode, dwell_seconds FROM events
                WHERE tenant_id=%s AND type='dwell' AND dwell_seconds IS NOT NULL
                ORDER BY time DESC LIMIT 200
                """,
                (tenant_id,),
            )
            dwell_rows = cur.fetchall()

            cur.execute(
                """
                SELECT DISTINCT ON (camera_id, zone_id) camera_id, zone_id, count, labels
                FROM events
                WHERE tenant_id=%s AND type='occupancy_sample' AND mode='staffing'
                ORDER BY camera_id, zone_id, time DESC
                """,
                (tenant_id,),
            )
            staffing_rows = cur.fetchall()

        occupancy = [{"camera_id": r[0], "zone_id": r[1], "count": r[2],
                      "ts": r[3].isoformat()} for r in occ_rows]
        parking = [{"camera_id": r[0], "zone_id": r[1],
                    "state": "parked" if r[2] == "vehicle_parked" else "free",
                    "ts": r[3].isoformat()} for r in park_rows]
        spaces_occupied = sum(1 for p in parking if p["state"] == "parked")

        waits = [r[1] for r in dwell_rows if _s(r[0]) == "queue" and r[1] is not None]
        avg_wait = round(sum(waits) / len(waits), 1) if waits else None
        browses = [r[1] for r in dwell_rows if _s(r[0]) == "retail" and r[1] is not None]
        avg_browse = round(sum(browses) / len(browses), 1) if browses else None

        stations_total = len(staffing_rows)
        stations_unstaffed = sum(1 for r in staffing_rows if (r[2] or 0) == 0)
        # Among staffed stations, split active vs static (labels jsonb -> list).
        _staffed = [r for r in staffing_rows if (r[2] or 0) > 0]
        stations_static = sum(1 for r in _staffed if "static" in (r[3] or []))
        stations_active = sum(1 for r in _staffed if "active" in (r[3] or []))

        return {
            "tenant_id": tenant_id,
            "totals": {
                "entries": entries, "exits": exits,
                "retail_occupancy": max(0, entries - exits),
                "parking_spaces_occupied": spaces_occupied,
                "avg_wait_seconds": avg_wait,
                "wait_samples": len(waits),
                "avg_browse_seconds": avg_browse,
                "browse_samples": len(browses),
                "ppe_violations": ppe_violations,
                "vehicle_crossings": vehicle_crossings,
                "stations_total": stations_total,
                "stations_unstaffed": stations_unstaffed,
                "stations_active": stations_active,
                "stations_static": stations_static,
            },
            "occupancy": occupancy,
            "parking": parking,
            "devices": self.device_health(tenant_id),
            "recent": self.recent_events(tenant_id, limit=25),
        }

    # --- Billing ------------------------------------------------------------ #

    def get_billing_account(self, tenant_id: str) -> dict | None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id, stripe_customer_id, stripe_subscription_item_id, plan
                   FROM billing_accounts WHERE tenant_id=%s""",
                (tenant_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"tenant_id": row[0], "stripe_customer_id": row[1],
                "stripe_subscription_item_id": row[2], "plan": row[3]}

    def upsert_billing_account(self, tenant_id: str, stripe_customer_id: str,
                               stripe_subscription_item_id: str, plan: str) -> None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO billing_accounts
                    (tenant_id, stripe_customer_id, stripe_subscription_item_id, plan)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    stripe_subscription_item_id = EXCLUDED.stripe_subscription_item_id,
                    plan = EXCLUDED.plan
                """,
                (tenant_id, stripe_customer_id, stripe_subscription_item_id, plan),
            )

    def all_billing_accounts(self) -> list[dict]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                """SELECT tenant_id, stripe_customer_id, stripe_subscription_item_id, plan
                   FROM billing_accounts"""
            )
            rows = cur.fetchall()
        return [{"tenant_id": r[0], "stripe_customer_id": r[1],
                 "stripe_subscription_item_id": r[2], "plan": r[3]} for r in rows]


def _s(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else getattr(value, "value", str(value))


def _scalar(cur) -> int:
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0
