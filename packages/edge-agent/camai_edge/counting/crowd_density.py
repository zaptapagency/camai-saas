"""Crowd density — live headcount vs a crush-risk threshold (Tier 1).

This is capacity with a crush-risk framing: instead of a shop's fire-code limit,
each zone's ``capacity`` is read as the *maximum safe headcount* before the crowd
is dense enough to risk a crush (a transit platform, a festival pen, a stairwell
landing, a venue entrance). It counts the people standing in the zone every frame
and raises a ``crowd_alert`` when the zone goes over that threshold.

A short grace window keeps a momentary spike (someone crossing through, a cluster
briefly overlapping the zone) from alarming: the zone only *commits* to over-threshold
after it has been over for ``_CROWD_GRACE_SECONDS``, and it emits exactly **one**
``crowd_alert`` on that commit. It does not fire again until the headcount has
dropped back to within the threshold (re-armed), so a packed platform produces one
alert, not one per frame. When the count later climbs back over past the grace, a
fresh alert fires.

Emissions per zone:
* ``crowd_alert`` once when the zone commits to over-threshold, carrying
  ``count`` = the headcount that tripped it and ``labels=["threshold:<N>"]``;
* ``occupancy_sample`` periodically (the live headcount tile / trend), tagged
  ``["over"]`` while the zone is committed over-threshold, else untagged;
* a same-zone ``occupancy_sample`` fired immediately whenever the committed
  over-threshold state flips (into or out of alert), so "back to safe" is as
  timely as the alert. Density over time is derived cloud-side from samples.

A zone with no configured ``capacity`` never alerts — it is still sampled, so a
threshold can be set later without losing the trend.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_SAMPLE_INTERVAL = 15.0       # seconds — cadence of the headcount sample
_CROWD_GRACE_SECONDS = 3.0    # over-threshold longer than this ⇒ commit + alarm


class CrowdDensityCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> committed "is over its crush threshold" state.
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

        # 1) Per-zone crush-risk state, with an immediate sample on any state flip.
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            head = sum(1 for d in people if point_in_polygon(*d.foot_point, poly))
            threshold = zone.capacity
            committed = self._over[zone.id]

            if threshold is not None and head > threshold:
                if self._over_since[zone.id] is None:
                    self._over_since[zone.id] = ts
                if not committed and (ts - self._over_since[zone.id]) >= _CROWD_GRACE_SECONDS:
                    # Commit: fire one alert, then the state-change sample.
                    self._over[zone.id] = True
                    events.append(self._event(
                        ts, EventType.crowd_alert, zone_id=zone.id,
                        count=head, labels=[f"threshold:{threshold}"],
                    ))
                    events.append(self._event(
                        ts, EventType.occupancy_sample, zone_id=zone.id,
                        count=head, labels=["over"],
                    ))
            else:
                # Within the threshold (or none set): re-arm for a future alert.
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
            threshold = "-" if zone.capacity is None else str(zone.capacity)
            label = f"{zone.id}: {'CRUSH RISK' if over else 'ok'} (max {threshold})"
            cv2.polylines(image, [pts], True, color, 2)
            cv2.putText(image, label, (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
