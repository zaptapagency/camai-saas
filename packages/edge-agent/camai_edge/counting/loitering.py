"""Loitering — a person dwelling in a zone too long.

For each person standing in a monitored zone, this tracks how long that person
has been continuously present and raises a ``loitering_alert`` once their dwell
time crosses ``_LOITER_SECONDS``. The alert carries the dwell duration (whole
seconds) in ``labels`` so the cloud can show "lingered 34s" without a schema
change. It also emits a periodic ``occupancy_sample`` of the zone headcount.

Dwell is measured per ``(zone, track)``: the first time a tracked person's
foot-point falls inside a zone we stamp a first-seen ts, and dwell is
``now - first_seen``. A short leave-grace absorbs brief occlusions — a track
that blinks out for less than ``_LEAVE_GRACE_SECONDS`` keeps its running timer,
so a momentary miss doesn't reset the dwell. Leaving for longer clears the
track so a genuine re-entry starts a fresh timer.

Alerts are debounced per ``(zone, track)``: exactly one event fires when a track
first crosses the threshold, and it won't re-fire while that track stays in the
zone. Tracks not seen for ``_TRACK_TTL_SECONDS`` are pruned to bound state over
long runs.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_LOITER_SECONDS = 30.0             # dwell this long in a zone ⇒ loitering alert
_OCCUPANCY_SAMPLE_INTERVAL = 15.0  # seconds — cadence of the zone headcount sample
_LEAVE_GRACE_SECONDS = 3.0         # unseen shorter than this keeps the dwell timer
_TRACK_TTL_SECONDS = 60.0          # forget tracks gone longer than this


class LoiteringCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> {track_id -> {"first_seen": ts, "last_seen": ts, "alerted": bool}}
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
                if not point_in_polygon(*p.foot_point, poly):
                    continue
                st = state.get(p.track_id)
                if st is None:
                    st = {"first_seen": ts, "last_seen": ts, "alerted": False}
                    state[p.track_id] = st
                st["last_seen"] = ts

                dwell = ts - st["first_seen"]
                if dwell >= _LOITER_SECONDS and not st["alerted"]:
                    st["alerted"] = True
                    events.append(self._event(
                        ts, EventType.loitering_alert, zone_id=zone.id,
                        object_class=ObjectClass.person, track_id=p.track_id,
                        count=1, labels=[f"{int(dwell)}s"], dwell_seconds=dwell,
                    ))

            # Drop tracks gone past the leave-grace so a real re-entry restarts the timer.
            gone = [tid for tid, s in state.items()
                    if ts - s["last_seen"] > _LEAVE_GRACE_SECONDS]
            for tid in gone:
                del state[tid]

        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                poly = resolve_zone(zone, width, height)
                head = sum(1 for p in persons if point_in_polygon(*p.foot_point, poly))
                events.append(self._event(ts, EventType.occupancy_sample,
                                          zone_id=zone.id, count=head))

        self._prune_tracks(ts)
        return events

    def _prune_tracks(self, ts: float) -> None:
        for state in self._state.values():
            stale = [tid for tid, s in state.items()
                     if ts - s["last_seen"] > _TRACK_TTL_SECONDS]
            for tid in stale:
                del state[tid]

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            cv2.polylines(image, [pts], True, (0, 140, 255), 2)
            cv2.putText(image, f"{zone.id}: loitering watch",
                        (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 1)
