"""Intrusion detection — a person present in a restricted / after-hours zone.

Each zone is a restricted area. Any tracked person whose foot point falls inside
a zone is an intruder, and the edge raises one ``intrusion_alert`` the moment that
track enters the zone. The "after-hours schedule" (when the zone is restricted) is
applied cloud-side / by config — the edge simply reports presence in the zone, so
a zone that is only off-limits at night still produces the raw signal the cloud
gates against the schedule.

Alerts are debounced per (zone, track): one event on entry, and no re-alert while
that track stays in the zone. A track that leaves re-arms only after it has been
out (or unseen) for ``_CLEAR_GRACE_SECONDS``, so a brief flicker at the boundary
doesn't double-fire. Tracks unseen for ``_TRACK_TTL_SECONDS`` are pruned to bound
state over long runs.

A periodic ``occupancy_sample`` per zone carries the current intruder headcount
(0 = clear), for the live tile and trend.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0   # seconds — cadence of the zone headcount sample
_CLEAR_GRACE_SECONDS = 5.0          # out of the zone this long ⇒ re-arm (re-alert on return)
_TRACK_TTL_SECONDS = 60.0           # forget tracks unseen this long


class IntrusionCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> {track_id -> {"in_zone": bool, "last_in_ts": ts, "last_seen": ts}}
        self._state: dict[str, dict[int, dict]] = {z.id: {} for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        persons = [d for d in detections
                   if d.object_class == ObjectClass.person and d.track_id is not None]

        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            state = self._state[zone.id]

            for p in persons:
                inside = point_in_polygon(*p.foot_point, poly)
                st = state.get(p.track_id)
                if st is None:
                    st = {"in_zone": False, "last_in_ts": -1e9, "last_seen": ts}
                    state[p.track_id] = st
                st["last_seen"] = ts

                if inside:
                    rearmed = (ts - st["last_in_ts"]) > _CLEAR_GRACE_SECONDS
                    if not st["in_zone"] and rearmed:
                        events.append(self._event(
                            ts, EventType.intrusion_alert, zone_id=zone.id,
                            object_class=ObjectClass.person, track_id=p.track_id,
                            count=1,
                        ))
                    st["in_zone"] = True
                    st["last_in_ts"] = ts
                else:
                    st["in_zone"] = False

            # Prune tracks unseen past the TTL.
            stale = [tid for tid, s in state.items()
                     if ts - s["last_seen"] > _TRACK_TTL_SECONDS]
            for tid in stale:
                del state[tid]

        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                poly = resolve_zone(zone, width, height)
                head = sum(1 for p in persons if point_in_polygon(*p.foot_point, poly))
                events.append(self._event(ts, EventType.occupancy_sample,
                                          zone_id=zone.id, count=head))
        return events

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            n = sum(1 for s in self._state[zone.id].values() if s["in_zone"])
            color = (0, 0, 255) if n else (0, 180, 0)
            cv2.polylines(image, [pts], True, color, 2)
            cv2.putText(image, f"{zone.id}: restricted ({n})",
                        (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
