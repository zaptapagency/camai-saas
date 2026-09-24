"""PPE / safety-compliance counting.

For each person standing in a required-PPE zone (a hazard area, a dock, a line),
this checks whether they're wearing the equipment that zone requires and raises a
``ppe_violation`` when they aren't — carrying *which* items are missing in the
event's ``labels`` (e.g. ``["helmet", "vest"]``). It also emits a periodic
``occupancy_sample`` of the zone headcount, so compliance rate = 1 − violators/head.

**Requires a PPE-trained detector.** COCO (the default model) has no helmet/vest
classes, so this mode only produces signal when the camera's ``detector.classes``
maps a safety model's class ids onto ``helmet``/``vest`` (and optionally the
explicit negatives ``no_helmet``/``no_vest``). See ``DetectorConfig.classes``.

Association is deliberately simple and robust: a PPE item belongs to the person
whose box contains the item's centre. That's enough for the top-down/wide views
these cameras use; a crowded-scene assignment (Hungarian on IoU) is the natural
upgrade once real footage shows it's needed.

Violations are debounced per (zone, track): one event when a person first appears
non-compliant, and again only if the *set* of missing items changes (they remove a
helmet). A short grace period clears a track after they leave so re-entry re-checks.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0  # seconds — cadence of the zone headcount sample
_LEAVE_GRACE_SECONDS = 3.0         # unseen-for longer than this ⇒ forget the track

# PPE item classes the detector may emit, split into positives (worn) and the
# explicit negatives some models output.
_POSITIVE = {ObjectClass.helmet, ObjectClass.vest}


class SafetyCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> {track_id -> {"last_seen": ts, "emitted": tuple|None}}
        self._state: dict[str, dict[int, dict]] = {z.id: {} for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        persons = [d for d in detections
                   if d.object_class == ObjectClass.person and d.track_id is not None]
        ppe_items = [d for d in detections if d.object_class in _POSITIVE]

        for zone in self.camera.zones:
            required = list(zone.required_ppe)
            if not required:
                continue  # safety is only meaningful where PPE is required
            poly = resolve_zone(zone, width, height)
            state = self._state[zone.id]
            in_zone: set[int] = set()

            for p in persons:
                if not point_in_polygon(*p.foot_point, poly):
                    continue
                in_zone.add(p.track_id)
                present = self._present_ppe(p, ppe_items)
                missing = [r for r in required if r not in present]

                st = state.setdefault(p.track_id, {"last_seen": ts, "emitted": None})
                st["last_seen"] = ts
                key = tuple(missing)
                if missing and st["emitted"] != key:
                    st["emitted"] = key
                    events.append(self._event(
                        ts, EventType.ppe_violation, zone_id=zone.id,
                        object_class=ObjectClass.person, track_id=p.track_id,
                        count=len(missing), labels=list(missing),
                    ))
                elif not missing:
                    st["emitted"] = ()  # compliant now; re-fire if PPE later removed

            # Forget tracks gone past the grace window.
            stale = [tid for tid, s in state.items()
                     if tid not in in_zone and ts - s["last_seen"] > _LEAVE_GRACE_SECONDS]
            for tid in stale:
                del state[tid]

        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                if not zone.required_ppe:
                    continue
                poly = resolve_zone(zone, width, height)
                head = sum(1 for p in persons if point_in_polygon(*p.foot_point, poly))
                events.append(self._event(ts, EventType.occupancy_sample,
                                          zone_id=zone.id, count=head))
        return events

    @staticmethod
    def _present_ppe(person: Detection, ppe_items: list[Detection]) -> set[str]:
        """PPE item classes whose centre falls inside this person's box."""
        present: set[str] = set()
        for item in ppe_items:
            cx, cy = item.centroid
            if person.x1 <= cx <= person.x2 and person.y1 <= cy <= person.y2:
                present.add(item.object_class.value)
        return present

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            if not zone.required_ppe:
                continue
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            n = len(self._state[zone.id])
            cv2.polylines(image, [pts], True, (0, 140, 255), 2)
            cv2.putText(image, f"{zone.id}: PPE required ({','.join(zone.required_ppe)})",
                        (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 1)
