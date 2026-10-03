"""Wrong-way driving — a vehicle travelling against the allowed direction.

Reuses the traffic line-crossing geometry: each monitored line has an allowed
flow direction (left→right across ``a``→``b`` is "forward"; flip ``invert`` when
calibration shows the permitted direction is the other way). A vehicle that crosses
a line in the *forward* (allowed) direction is normal traffic and produces no
event; one that crosses in the *reverse* direction raises a ``wrong_way_alert``
carrying ``["wrong_way"]`` in ``labels`` and the vehicle type in ``object_class``.

No new model: the same COCO detector that recognises vehicles drives this; an
optional ``detector.classes`` map still breaks the offender down by vehicle type.
One alert per direction-change per track (idempotent), so a vehicle reversing back
and forth over the line alerts once each time it actually goes the wrong way.

Buyers: smart-city one-way streets, parking-garage ramps, logistics-yard lanes.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import line_side, resolve_line

# Everything road-going the counter treats as a vehicle (plain COCO yields
# `vehicle`; a class map may yield the finer types).
_VEHICLE_LIKE = {
    ObjectClass.vehicle, ObjectClass.car, ObjectClass.truck,
    ObjectClass.bus, ObjectClass.motorcycle, ObjectClass.bicycle,
}


class WrongWayCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # (line_id, track_id) -> last signed side (+1 / -1)
        self._last_side: dict[tuple[str, int], int] = {}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        vehicles = [
            d for d in detections
            if d.object_class in _VEHICLE_LIKE and d.track_id is not None
        ]

        for line in self.camera.lines:
            ax, ay, bx, by = resolve_line(line, width, height)
            for det in vehicles:
                fx, fy = det.foot_point
                side = 1 if line_side(fx, fy, ax, ay, bx, by) >= 0 else -1
                key = (line.id, det.track_id)
                prev = self._last_side.get(key)
                self._last_side[key] = side
                if prev is None or prev == side:
                    continue

                # left(+1) -> right(-1) is "forward" (allowed) by default; invert flips.
                forward = prev > side
                if line.invert:
                    forward = not forward
                if forward:
                    continue  # allowed direction — normal traffic, no alert

                events.append(self._event(
                    ts, EventType.wrong_way_alert, line_id=line.id,
                    object_class=det.object_class, track_id=det.track_id,
                    count=1, labels=["wrong_way"],
                ))

        self._forget_stale(vehicles)
        return events

    def _forget_stale(self, current: list[Detection]) -> None:
        live = {d.track_id for d in current}
        for k in [k for k in self._last_side if k[1] not in live]:
            del self._last_side[k]

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2

        width, height = frame_size
        for line in self.camera.lines:
            ax, ay, bx, by = resolve_line(line, width, height)
            cv2.line(image, (int(ax), int(ay)), (int(bx), int(by)), (60, 180, 255), 2)
            cv2.putText(image, f"{line.id}: wrong-way watch", (int(ax), int(ay) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 180, 255), 1)
