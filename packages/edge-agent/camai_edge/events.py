"""Local event queue with durable buffering.

Events are written to a small SQLite file first, then drained in batches to the
cloud. This is what lets a brief connectivity drop not lose events (buffer hours,
not days) and makes delivery idempotent: nothing is deleted from the queue until
the cloud acknowledges the batch.

SQLite is deliberate — it's embedded, crash-safe, and needs no extra service on
the edge box.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from camai_schema import Event


class EventQueue:
    def __init__(self, path: str | Path = "event-queue.db") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                ts       TEXT NOT NULL,
                payload  TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def put(self, event: Event) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO events (event_id, ts, payload) VALUES (?, ?, ?)",
                (event.event_id, event.ts.isoformat(), event.model_dump_json()),
            )
            self._conn.commit()

    def peek(self, limit: int = 500) -> list[Event]:
        """Return up to ``limit`` oldest events without removing them."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM events ORDER BY ts LIMIT ?", (limit,)
            ).fetchall()
        return [Event.model_validate(json.loads(r[0])) for r in rows]

    def ack(self, event_ids: list[str]) -> None:
        """Remove events the cloud durably accepted."""
        if not event_ids:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM events WHERE event_id = ?", [(e,) for e in event_ids]
            )
            self._conn.commit()

    def depth(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(n)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
