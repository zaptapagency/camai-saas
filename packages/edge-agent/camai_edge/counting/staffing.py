"""Workstation coverage — is a work area staffed, or sitting empty?

For a restaurant (or any operation with fixed stations), each zone is a work area:
the prep line, the counter, the grill, the dishwash. This counts *people present in
the station* and answers the operational question "is this station being worked
right now, and how long has it been left unattended?" — as an **anonymous, station
-level** signal. It never identifies individuals or scores a person's productivity;
that's deliberate (CamAI does no biometric identification) and it's also what keeps
this use legal and sellable.

Emissions per station zone:
* ``occupancy_sample`` with the current staff headcount (0 = unstaffed), sampled
  periodically for the live tile and trend;
* a same-zone ``occupancy_sample`` fired immediately on a *state change* — staffed
  ⇄ unstaffed — so an "prep line unmanned during the rush" alert is timely.

A grace period means a station only counts as unstaffed after it's been empty for
``_UNSTAFFED_GRACE_SECONDS`` — someone stepping out of frame for a moment doesn't
trip a false alert. Coverage % over a shift is derived cloud-side from the samples.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0   # seconds — cadence of the staffed-headcount tile
_UNSTAFFED_GRACE_SECONDS = 10.0     # empty longer than this ⇒ the station is unstaffed


class StaffingCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> committed "is staffed" state and the last time someone was in it
        self._staffed: dict[str, bool] = {z.id: False for z in camera.zones}
        # Far in the past so a station empty at startup is unstaffed, not "in grace".
        self._last_present_ts: dict[str, float] = {z.id: -1e9 for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        people = [d for d in detections if d.object_class == ObjectClass.person]

        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            head = sum(1 for d in people if point_in_polygon(*d.foot_point, poly))

            if head > 0:
                self._last_present_ts[zone.id] = ts
                staffed_now = True
            else:
                # Stay "staffed" until the station has been empty past the grace window.
                staffed_now = (ts - self._last_present_ts[zone.id]) <= _UNSTAFFED_GRACE_SECONDS

            if staffed_now != self._staffed[zone.id]:
                self._staffed[zone.id] = staffed_now
                # Immediate sample on the transition so staffed/unstaffed is timely.
                events.append(self._event(ts, EventType.occupancy_sample,
                                          zone_id=zone.id, count=head))

        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                poly = resolve_zone(zone, width, height)
                head = sum(1 for d in people if point_in_polygon(*d.foot_point, poly))
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
            staffed = self._staffed[zone.id]
            color = (0, 180, 0) if staffed else (0, 0, 255)
            label = "staffed" if staffed else "UNSTAFFED"
            cv2.polylines(image, [pts], True, color, 2)
            cv2.putText(image, f"{zone.id}: {label}", (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
