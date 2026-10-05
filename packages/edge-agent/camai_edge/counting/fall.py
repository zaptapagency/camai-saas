"""Fall / slip detection — a person who collapsed and stayed down.

For each tracked person this watches the *shape* of their bounding box: an upright
person's box is tall (height > width), a person who has fallen or slipped is wide
(width ≳ height). When a track's aspect ratio (width / height) crosses
``_FALL_ASPECT`` and stays there continuously for ``_FALL_CONFIRM_SECONDS`` — long
enough to rule out bending down, sitting, or a single bad frame — this raises a
``fall_alert`` carrying how long they've been down (whole seconds) in ``labels``.

No new model: it runs on the ordinary person detector the rest of the pipeline
already uses. Falls matter anywhere in view, so detection is frame-wide by default;
if the camera has zones drawn, only people whose foot point is inside a zone are
watched (e.g. a ward, an aisle), so a fall off-camera-of-interest doesn't alert.

Alerts are debounced per track: exactly one fires when a track first stays down
past the confirm window, and it won't re-fire while that person remains down. The
track re-arms once they're clearly upright again (hysteresis: ``_STAND_ASPECT`` is
below ``_FALL_ASPECT`` so a borderline box doesn't flap), so a genuine second fall
alerts again. Tracks unseen past a short grace are dropped so a re-entry starts
fresh; a longer TTL bounds state over long runs.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_FALL_ASPECT = 1.0          # box width/height at or above this ⇒ "down"
_STAND_ASPECT = 0.7         # back below this ⇒ "upright" (re-arm); gap = hysteresis
_FALL_CONFIRM_SECONDS = 2.0  # must stay down this long before alerting
_LEAVE_GRACE_SECONDS = 3.0  # unseen longer than this ⇒ forget the track (re-entry is fresh)


class FallCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # track_id -> {"down_since": ts|None, "alerted": bool, "last_seen": ts}
        self._state: dict[int, dict] = {}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        persons = [d for d in detections
                   if d.object_class == ObjectClass.person and d.track_id is not None]
        polys = [resolve_zone(z, width, height) for z in self.camera.zones]

        for p in persons:
            if polys and not any(point_in_polygon(*p.foot_point, poly) for poly in polys):
                continue

            box_h = p.y2 - p.y1
            if box_h <= 0:
                continue
            aspect = (p.x2 - p.x1) / box_h

            st = self._state.setdefault(
                p.track_id, {"down_since": None, "alerted": False, "last_seen": ts})
            st["last_seen"] = ts

            if aspect >= _FALL_ASPECT:
                if st["down_since"] is None:
                    st["down_since"] = ts
                down_for = ts - st["down_since"]
                if down_for >= _FALL_CONFIRM_SECONDS and not st["alerted"]:
                    st["alerted"] = True
                    events.append(self._event(
                        ts, EventType.fall_alert,
                        object_class=ObjectClass.person, track_id=p.track_id,
                        count=1, labels=[f"{int(down_for)}s"], dwell_seconds=down_for,
                    ))
            elif aspect < _STAND_ASPECT:
                # Clearly upright again — re-arm for a future fall.
                st["down_since"] = None
                st["alerted"] = False
            # In-between (crouching/sitting): leave state unchanged, don't flap.

        self._drop_stale(ts)
        return events

    def _drop_stale(self, ts: float) -> None:
        gone = [tid for tid, s in self._state.items()
                if ts - s["last_seen"] > _LEAVE_GRACE_SECONDS]
        for tid in gone:
            del self._state[tid]

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            cv2.polylines(image, [pts], True, (0, 140, 255), 2)
            cv2.putText(image, f"{zone.id}: fall watch",
                        (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 1)
