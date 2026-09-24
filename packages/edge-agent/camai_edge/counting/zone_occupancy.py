"""Zone-based counting for parking and warehouse.

Both verticals reduce to "how many relevant objects have their foot point inside
this polygon right now", differing only in what they count and which events they
emit:

* **parking**  — one zone == one space; count vehicles. A space going 0 -> >=1
  emits ``vehicle_parked``; >=1 -> 0 emits ``vehicle_left``.
* **warehouse**— count people/forklifts/pallets in a bay; any change in the
  integer count emits a signed ``count_delta``. (Beta: stacked/occluded objects
  are the hardest CV case and need per-site tuning.)

Both emit a periodic ``occupancy_sample`` per zone for trend charts.

A short debounce (a state must hold for a few samples before it's committed)
absorbs single-frame detection flicker and brief occlusions — the main source of
false transitions in the field.
"""

from __future__ import annotations

from camai_schema import Event, EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter, relevant_classes_for
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0  # seconds — cadence of the live occupancy tile
_DEBOUNCE_SAMPLES = 3              # consecutive frames a new count must hold


class ZoneOccupancyCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        self._mode = Mode(camera.mode)
        self._relevant = relevant_classes_for(self._mode)
        # committed integer count per zone
        self._count: dict[str, int] = {z.id: 0 for z in camera.zones}
        # pending (candidate) count and how many frames it has held
        self._pending: dict[str, tuple[int, int]] = {z.id: (0, 0) for z in camera.zones}
        # count as-of the last committed transition, so deltas/transitions are correct
        self._prev_count: dict[str, int] = {z.id: 0 for z in camera.zones}
        # instantaneous raw count from the most recent frame, per zone
        self._raw: dict[str, int] = {z.id: 0 for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        relevant = [
            d for d in detections
            if d.object_class in self._relevant
        ]

        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            raw_count = sum(
                1 for d in relevant if point_in_polygon(*d.foot_point, poly)
            )
            self._raw[zone.id] = raw_count
            committed = self._commit(zone.id, raw_count)
            if committed is None:
                continue  # no debounced change yet
            events.extend(self._emit_transition(zone.id, committed, ts))

        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                # Report the instantaneous count (how many are in the zone right
                # now) for the live tile; transition events above stay debounced.
                events.append(
                    self._event(
                        ts, EventType.occupancy_sample,
                        zone_id=zone.id, count=self._raw[zone.id],
                    )
                )
        return events

    def _commit(self, zone_id: str, raw_count: int) -> int | None:
        """Debounce raw per-frame counts; return the new committed count on change."""
        current = self._count[zone_id]
        if raw_count == current:
            self._pending[zone_id] = (current, 0)
            return None

        candidate, held = self._pending[zone_id]
        if raw_count == candidate:
            held += 1
        else:
            candidate, held = raw_count, 1
        self._pending[zone_id] = (candidate, held)

        if held >= _DEBOUNCE_SAMPLES:
            self._count[zone_id] = candidate
            self._pending[zone_id] = (candidate, 0)
            return candidate
        return None

    def _emit_transition(self, zone_id: str, new_count: int, ts: float) -> list[Event]:
        prev = self._prev_count.get(zone_id, 0)
        self._prev_count[zone_id] = new_count

        if self._mode == Mode.parking:
            if prev == 0 and new_count >= 1:
                return [self._event(ts, EventType.vehicle_parked, zone_id=zone_id,
                                    object_class=ObjectClass.vehicle)]
            if prev >= 1 and new_count == 0:
                return [self._event(ts, EventType.vehicle_left, zone_id=zone_id,
                                    object_class=ObjectClass.vehicle)]
            return []

        # warehouse: signed net change
        delta = new_count - prev
        if delta == 0:
            return []
        return [self._event(ts, EventType.count_delta, zone_id=zone_id, delta=delta)]

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            occupied = self._count[zone.id] > 0
            color = (0, 0, 255) if occupied else (0, 200, 0)
            cv2.polylines(image, [pts], isClosed=True, color=color, thickness=2)
            cx, cy = pts[0]
            cv2.putText(
                image, f"{zone.id}:{self._count[zone.id]}", (int(cx), int(cy) - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
            )
