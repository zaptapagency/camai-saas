"""Tailgating / piggybacking detection: multiple people through a secure line together.

A secure door, mantrap, or turnstile is drawn as a ``line`` in the camera config.
Each authorized entry should be ONE person crossing that line. When two or more
distinct people cross the SAME line within a short window of each other, that's a
tailgating event — someone slipping through on another person's badge swipe.

Crossing detection reuses the exact mechanism in ``line_crossing.py`` /
``traffic.py``: per (line, track) we remember the signed ``line_side`` of the
foot point; a sign change between frames is one crossing, recorded with its
timestamp. We then cluster crossing timestamps per line: as soon as >= 2 distinct
tracks cross the same line within ``_TAILGATE_WINDOW`` seconds, we emit one
``tailgating_alert`` for that cluster and start a fresh window, so the same
crossings are never counted into two overlapping alerts.

Only person-class detections are considered. Pure-Python, no numpy/opencv.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import line_side, resolve_line

# People crossing the same line within this many seconds of one another are
# treated as one tailgating cluster.
_TAILGATE_WINDOW = 2.0


class TailgatingCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # (line_id, track_id) -> last signed side (+1 / -1)
        self._last_side: dict[tuple[str, int], int] = {}
        # line_id -> list of (ts, track_id) for crossings in the open cluster window.
        self._pending: dict[str, list[tuple[float, int]]] = {}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        people = [
            d for d in detections
            if d.object_class == ObjectClass.person and d.track_id is not None
        ]

        for line in self.camera.lines:
            ax, ay, bx, by = resolve_line(line, width, height)
            for det in people:
                fx, fy = det.foot_point
                side = 1 if line_side(fx, fy, ax, ay, bx, by) >= 0 else -1
                key = (line.id, det.track_id)
                prev = self._last_side.get(key)
                self._last_side[key] = side
                if prev is None or prev == side:
                    continue

                # A crossing happened for this track on this line.
                self._record_crossing(line.id, ts, det.track_id)

            ev = self._flush_if_tailgating(line.id, ts)
            if ev is not None:
                events.append(ev)

        self._forget_stale(people)
        return events

    def _record_crossing(self, line_id: str, ts: float, track_id: int) -> None:
        """Add a crossing to the line's open window, dropping anything stale."""
        window = [
            (t, tid) for (t, tid) in self._pending.get(line_id, [])
            if ts - t <= _TAILGATE_WINDOW
        ]
        # One crossing per track per cluster — re-use latest timestamp if it recrosses.
        window = [(t, tid) for (t, tid) in window if tid != track_id]
        window.append((ts, track_id))
        self._pending[line_id] = window

    def _flush_if_tailgating(self, line_id: str, ts: float) -> Event | None:
        """Emit one alert and reset the window once >= 2 distinct tracks clustered."""
        window = [
            (t, tid) for (t, tid) in self._pending.get(line_id, [])
            if ts - t <= _TAILGATE_WINDOW
        ]
        self._pending[line_id] = window

        distinct = {tid for (_t, tid) in window}
        if len(distinct) < 2:
            return None

        n = len(distinct)
        # Cluster fired: start a fresh window so these crossings never re-fire.
        self._pending[line_id] = []
        return self._event(
            ts,
            EventType.tailgating_alert,
            line_id=line_id,
            object_class=ObjectClass.person,
            count=n,
            labels=[f"{n}_together"],
        )

    def _forget_stale(self, current: list[Detection]) -> None:
        """Drop side-state for ids no longer present, bounding memory over long runs."""
        live = {d.track_id for d in current}
        for k in [k for k in self._last_side if k[1] not in live]:
            del self._last_side[k]

    def draw(self, image, frame_size):  # pragma: no cover
        return None
