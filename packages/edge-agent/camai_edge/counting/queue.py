"""Queue-length counting: how many people are waiting, and how long they wait.

A queue zone is a polygon over a waiting area (checkout line, service desk, taxi
rank). This counter reports two things retail/parking don't:

* **queue length** — the instantaneous number of people whose foot point is inside
  the zone, emitted periodically as an ``occupancy_sample`` (the live tile);
* **wait time** — when a tracked person leaves the zone, a ``dwell`` event carries
  how long they were in it. Averaged in the cloud, that's the "average wait" metric
  a store manager actually cares about.

Membership is smoothed by a short grace period: a person must be *unseen* for
``_LEAVE_GRACE_SECONDS`` before they count as having left, so a one-frame miss or a
brief occlusion in a packed line doesn't end (and restart) their wait. That makes
wait times robust in exactly the crowded conditions queues create.

Only people are counted. ``Zone.capacity`` (optional) is the natural threshold for
a "queue too long — open another till" alert, surfaced cloud-side.
"""

from __future__ import annotations

from dataclasses import dataclass

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0  # seconds — cadence of the live queue-length tile
_LEAVE_GRACE_SECONDS = 2.0         # unseen-for longer than this ⇒ the person left


@dataclass
class _Waiter:
    """One person's presence in a queue zone."""

    enter_ts: float
    last_seen_ts: float


class QueueCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> {track_id -> _Waiter}
        self._waiting: dict[str, dict[int, _Waiter]] = {z.id: {} for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        people = [
            d for d in detections
            if d.object_class == ObjectClass.person and d.track_id is not None
        ]

        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            waiters = self._waiting[zone.id]

            inside_now = {
                d.track_id for d in people if point_in_polygon(*d.foot_point, poly)
            }

            # Update / register everyone currently in the zone.
            for tid in inside_now:
                w = waiters.get(tid)
                if w is None:
                    waiters[tid] = _Waiter(enter_ts=ts, last_seen_ts=ts)
                else:
                    w.last_seen_ts = ts

            # Anyone unseen beyond the grace window has left → emit their wait time.
            left = [
                tid for tid, w in waiters.items()
                if tid not in inside_now and (ts - w.last_seen_ts) > _LEAVE_GRACE_SECONDS
            ]
            for tid in left:
                w = waiters.pop(tid)
                events.append(
                    self._event(
                        w.last_seen_ts, EventType.dwell,
                        zone_id=zone.id, object_class=ObjectClass.person,
                        track_id=tid, dwell_seconds=round(w.last_seen_ts - w.enter_ts, 2),
                    )
                )

        # Periodic queue-length sample (instantaneous count in each zone).
        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                length = self._current_length(zone.id, frame_size, people, ts)
                events.append(
                    self._event(ts, EventType.occupancy_sample,
                                zone_id=zone.id, count=length)
                )
        return events

    def _current_length(self, zone_id, frame_size, people, ts) -> int:
        """People physically in the zone this frame (not the grace-retained set)."""
        width, height = frame_size
        poly = resolve_zone(next(z for z in self.camera.zones if z.id == zone_id),
                            width, height)
        return sum(1 for d in people if point_in_polygon(*d.foot_point, poly))

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            length = len(self._waiting[zone.id])
            over = zone.capacity is not None and length > zone.capacity
            color = (0, 0, 255) if over else (0, 180, 220)
            cv2.polylines(image, [pts], isClosed=True, color=color, thickness=2)
            cv2.putText(image, f"{zone.id}: {length} waiting", (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
