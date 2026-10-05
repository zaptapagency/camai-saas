"""Abandoned-object detection — an unattended bag/object left static too long.

For each tracked object in a monitored scope this tracks how long it has sat in
essentially the same place, and raises an ``abandoned_object_alert`` once it has
been stationary continuously for ``_ABANDON_SECONDS``. The alert carries how long
it has been sitting (whole seconds) in ``labels``.

"Stationary" is measured from an anchor point: the first position we see a track
at. While its centroid stays within ``_MOVE_TOLERANCE`` (a fraction of the frame
diagonal) of that anchor the dwell timer runs; if it moves further the object was
picked up or shifted, so the anchor and timer reset (and the alert re-arms). A
short leave-grace absorbs brief detection dropouts so a flicker doesn't reset a
genuine abandonment.

**Needs object classes the default model doesn't emit.** Plain COCO detects bags
(backpack/handbag/suitcase) but the edge detector only keeps person/vehicle by
default; map a model's bag ids onto ``bag`` via ``detector.classes`` to feed this
mode. Scope is per-zone when the camera has zones (membership by centroid), else a
single scene scope — so a concourse camera with no zones still works.

Alerts are debounced per (scope, track): one event when a track first crosses the
threshold, none while it keeps sitting. Tracks unseen past the leave-grace are
dropped so a genuine re-appearance starts a fresh timer.
"""

from __future__ import annotations

from math import hypot

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_ABANDON_SECONDS = 20.0     # stationary this long in a scope ⇒ abandoned-object alert
_MOVE_TOLERANCE = 0.05      # centroid drift (fraction of frame diagonal) still "stationary"
_LEAVE_GRACE_SECONDS = 3.0  # unseen longer than this ⇒ forget the track

# Object classes this counter treats as a candidate unattended object.
_OBJECT = {ObjectClass.bag}


class AbandonedObjectCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # scope id (zone id, or None for scene) -> {track_id -> state}
        self._state: dict[str | None, dict[int, dict]] = {}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []
        tol = _MOVE_TOLERANCE * hypot(width, height)

        objects = [d for d in detections
                   if d.object_class in _OBJECT and d.track_id is not None]

        for scope_id, poly in self._scopes(width, height):
            state = self._state.setdefault(scope_id, {})

            for obj in objects:
                cx, cy = obj.centroid
                if poly is not None and not point_in_polygon(cx, cy, poly):
                    continue

                st = state.get(obj.track_id)
                if st is None or hypot(cx - st["anchor"][0], cy - st["anchor"][1]) > tol:
                    # New track, or it moved beyond tolerance: (re)anchor and re-arm.
                    st = {"anchor": (cx, cy), "first_seen": ts,
                          "last_seen": ts, "alerted": False}
                    state[obj.track_id] = st
                st["last_seen"] = ts

                dwell = ts - st["first_seen"]
                if dwell >= _ABANDON_SECONDS and not st["alerted"]:
                    st["alerted"] = True
                    events.append(self._event(
                        ts, EventType.abandoned_object_alert, zone_id=scope_id,
                        object_class=ObjectClass.bag, track_id=obj.track_id,
                        count=1, labels=[f"{int(dwell)}s"], dwell_seconds=dwell,
                    ))

            stale = [tid for tid, s in state.items()
                     if ts - s["last_seen"] > _LEAVE_GRACE_SECONDS]
            for tid in stale:
                del state[tid]

        return events

    def _scopes(self, width: int, height: int):
        """Yield (scope_id, polygon) pairs. Scene scope (None) when no zones."""
        if self.camera.zones:
            for zone in self.camera.zones:
                yield zone.id, resolve_zone(zone, width, height)
        else:
            yield None, None

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            cv2.polylines(image, [pts], True, (0, 140, 255), 2)
            cv2.putText(image, f"{zone.id}: unattended-object watch",
                        (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 1)
