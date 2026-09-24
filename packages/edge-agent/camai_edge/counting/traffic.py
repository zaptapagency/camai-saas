"""Smart-city traffic counting: directional vehicle flow across a line.

The core traffic metric is volume by direction — how many vehicles cross a line
each way (a road cordon, an approach, a turning movement). This mirrors the retail
line-crossing geometry but counts vehicles and, instead of retail-semantic
entry/exit, emits a ``vehicle_crossing`` event whose ``labels`` carry the direction
(``["forward"]`` / ``["reverse"]``) and whose ``object_class`` carries the vehicle
type when a class-aware model is configured.

Reuses what's already shipped:
* the same COCO detector already recognizes cars/buses/trucks/motorcycles (all map
  to ``vehicle`` by default) — so this vertical needs **no new model**;
* a ``detector.classes`` map can optionally split them into ``car``/``truck``/``bus``…
  so volume breaks down by type, at no extra pipeline cost.

Volume/flow-rate over time is derived cloud-side from the crossing timestamps; the
edge just emits one event per crossing (idempotent per direction change per track).
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import line_side, resolve_line

# Everything road-going the counter treats as a vehicle. Plain COCO yields
# `vehicle`; a class map may yield the finer types.
_VEHICLE_LIKE = {
    ObjectClass.vehicle, ObjectClass.car, ObjectClass.truck,
    ObjectClass.bus, ObjectClass.motorcycle, ObjectClass.bicycle,
}


class TrafficCounter(BaseCounter):
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

                # left(+1) -> right(-1) is "forward" by default; invert flips it.
                forward = prev > side
                if line.invert:
                    forward = not forward
                direction = "forward" if forward else "reverse"

                events.append(self._event(
                    ts, EventType.vehicle_crossing, line_id=line.id,
                    object_class=det.object_class, track_id=det.track_id,
                    count=1, labels=[direction],
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
            cv2.putText(image, line.id, (int(ax), int(ay) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 180, 255), 1)
