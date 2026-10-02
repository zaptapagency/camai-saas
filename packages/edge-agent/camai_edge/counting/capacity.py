"""Capacity — live headcount vs a configured occupancy limit (Tier 1).

Each zone carries a ``capacity`` (its occupancy limit). This counts the people
standing in the zone every frame and raises a ``capacity_breach`` when the zone
goes over that limit — for a shop's fire-code max, a waiting area, a platform, a
gallery room. It is the simplest of the verticals: no PPE model, no per-track
motion, just "how many are in here, and is that too many?".

A short grace window keeps a momentary spike (someone crossing through, two boxes
briefly overlapping the zone) from alarming: the zone only *commits* to over-limit
after it has been over for ``_BREACH_GRACE_SECONDS``, and it emits exactly **one**
``capacity_breach`` on that commit. It does not fire again until the zone has
dropped back to within the limit (re-armed), so a room that sits over its limit for
an hour produces one alert, not one per frame. When the count later falls back
over the limit past the grace, a fresh breach fires.

Emissions per zone:
* ``capacity_breach`` once when the zone commits to over-limit, carrying
  ``count`` = the headcount that tripped it and ``labels=["limit:<N>"]``;
* ``occupancy_sample`` periodically (the live headcount tile / trend), tagged
  ``["over"]`` while the zone is committed over-limit, else untagged;
* a same-zone ``occupancy_sample`` fired immediately whenever the committed
  over-limit state flips (into or out of breach), so the "back to normal" is as
  timely as the breach. Occupancy % over time is derived cloud-side from samples.

A zone with no configured ``capacity`` never breaches — it is still sampled, so a
limit can be set later without losing the trend.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_SAMPLE_INTERVAL = 15.0        # seconds — cadence of the headcount sample
_BREACH_GRACE_SECONDS = 5.0    # over-limit longer than this ⇒ commit + alarm


class CapacityCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> committed "is over its limit" state.
        self._over: dict[str, bool] = {z.id: False for z in camera.zones}
        # zone_id -> ts the zone first went over on this spell (None while within).
        self._over_since: dict[str, float | None] = {z.id: None for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        people = [d for d in detections if d.object_class == ObjectClass.person]

        # 1) Per-zone breach state, with an immediate sample on any state flip.
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            head = sum(1 for d in people if point_in_polygon(*d.foot_point, poly))
            limit = zone.capacity
            committed = self._over[zone.id]

            if limit is not None and head > limit:
                if self._over_since[zone.id] is None:
                    self._over_since[zone.id] = ts
                if not committed and (ts - self._over_since[zone.id]) >= _BREACH_GRACE_SECONDS:
                    # Commit: fire one breach, then the state-change sample.
                    self._over[zone.id] = True
                    events.append(self._event(
                        ts, EventType.capacity_breach, zone_id=zone.id,
                        count=head, labels=[f"limit:{limit}"],
                    ))
                    events.append(self._event(
                        ts, EventType.occupancy_sample, zone_id=zone.id,
                        count=head, labels=["over"],
                    ))
            else:
                # Within the limit (or no limit): re-arm for a future breach.
                self._over_since[zone.id] = None
                if committed:
                    self._over[zone.id] = False
                    events.append(self._event(
                        ts, EventType.occupancy_sample, zone_id=zone.id,
                        count=head, labels=None,
                    ))

        # 2) Periodic sample per zone (headcount + current committed state).
        if ts - self._last_sample_ts >= _SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                poly = resolve_zone(zone, width, height)
                head = sum(1 for d in people if point_in_polygon(*d.foot_point, poly))
                events.append(self._event(
                    ts, EventType.occupancy_sample, zone_id=zone.id,
                    count=head, labels=["over"] if self._over[zone.id] else None,
                ))
        return events

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            over = self._over[zone.id]
            color = (0, 0, 255) if over else (0, 180, 0)
            limit = "-" if zone.capacity is None else str(zone.capacity)
            label = f"{zone.id}: {'OVER' if over else 'ok'} (limit {limit})"
            cv2.polylines(image, [pts], True, color, 2)
            cv2.putText(image, label, (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
