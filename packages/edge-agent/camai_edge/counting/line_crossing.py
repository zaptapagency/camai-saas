"""Retail foot-traffic counting: virtual line-crossing + running occupancy + dwell.

For each configured line we remember which side each tracked id was last seen on.
When a track's foot point moves from one side to the other, that's one crossing:
left->right is an ``entry``, right->left an ``exit`` (flip per line with ``invert``
if calibration shows the door faces the other way). Running occupancy = cumulative
entries - exits, emitted as a periodic ``occupancy_sample``.

If the retail camera also has ``zones`` drawn (a department, an aisle, an
end-cap), we additionally track **dwell time**: how long each shopper's foot point
stayed inside a zone. When they leave, a ``dwell`` event carries the seconds spent
there — the "how long do people linger in produce" metric a merchandiser wants.
Membership uses the same short grace period as the queue counter so a one-frame
miss or a brief occlusion doesn't end (and restart) a dwell. A camera with no
zones emits no dwell events, so line-only retail setups are unchanged.

Only person-class detections are counted here.
"""

from __future__ import annotations

from dataclasses import dataclass

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import line_side, point_in_polygon, resolve_line, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 30.0  # seconds
_DWELL_LEAVE_GRACE_SECONDS = 2.0   # unseen-in-zone longer than this ⇒ the shopper left


@dataclass
class _Presence:
    """One shopper's presence in a dwell zone."""

    enter_ts: float
    last_seen_ts: float


class LineCrossingCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # (line_id, track_id) -> last signed side (+1 / -1)
        self._last_side: dict[tuple[str, int], int] = {}
        self._occupancy = 0
        self._last_sample_ts = 0.0
        # zone_id -> {track_id -> _Presence} for dwell-time tracking.
        self._in_zone: dict[str, dict[int, _Presence]] = {z.id: {} for z in camera.zones}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        people = [d for d in detections if d.object_class == ObjectClass.person and d.track_id is not None]

        for line in self.camera.lines:
            ax, ay, bx, by = resolve_line(line, width, height)
            for det in people:
                fx, fy = det.foot_point
                raw = line_side(fx, fy, ax, ay, bx, by)
                side = 1 if raw >= 0 else -1
                key = (line.id, det.track_id)
                prev = self._last_side.get(key)
                self._last_side[key] = side

                if prev is None or prev == side:
                    continue

                # A crossing happened. left(+1)->right(-1) is an entry by default.
                entering = prev > side
                if line.invert:
                    entering = not entering

                if entering:
                    self._occupancy += 1
                    ev_type = EventType.entry
                else:
                    self._occupancy = max(0, self._occupancy - 1)
                    ev_type = EventType.exit

                events.append(
                    self._event(
                        ts,
                        ev_type,
                        line_id=line.id,
                        object_class=ObjectClass.person,
                        track_id=det.track_id,
                    )
                )

        # Zone dwell: how long each shopper lingers in a drawn zone.
        events.extend(self._update_dwell(people, frame_size, ts))

        # Periodic occupancy snapshot for the dashboard's live tile.
        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            events.append(
                self._event(ts, EventType.occupancy_sample, count=self._occupancy)
            )

        self._forget_stale(people)
        return events

    def _update_dwell(
        self, people: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        """Track per-zone presence and emit a ``dwell`` event when a shopper leaves.

        Mirrors the queue counter's grace-window membership: a track must be unseen
        in a zone for longer than ``_DWELL_LEAVE_GRACE_SECONDS`` before it counts as
        having left, so brief occlusions don't chop one visit into several.
        """
        width, height = frame_size
        events: list[Event] = []

        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            present = self._in_zone[zone.id]

            inside_now = {
                d.track_id for d in people if point_in_polygon(*d.foot_point, poly)
            }

            for tid in inside_now:
                p = present.get(tid)
                if p is None:
                    present[tid] = _Presence(enter_ts=ts, last_seen_ts=ts)
                else:
                    p.last_seen_ts = ts

            left = [
                tid for tid, p in present.items()
                if tid not in inside_now and (ts - p.last_seen_ts) > _DWELL_LEAVE_GRACE_SECONDS
            ]
            for tid in left:
                p = present.pop(tid)
                events.append(
                    self._event(
                        p.last_seen_ts, EventType.dwell,
                        zone_id=zone.id, object_class=ObjectClass.person,
                        track_id=tid, dwell_seconds=round(p.last_seen_ts - p.enter_ts, 2),
                    )
                )

        return events

    def _forget_stale(self, current: list[Detection]) -> None:
        """Drop side-state for ids no longer present, bounding memory over long runs."""
        live = {d.track_id for d in current}
        stale = [k for k in self._last_side if k[1] not in live]
        for k in stale:
            del self._last_side[k]

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            waiting = len(self._in_zone[zone.id])
            cv2.polylines(image, [pts], isClosed=True, color=(0, 180, 220), thickness=2)
            cv2.putText(
                image, f"{zone.id}: {waiting} dwelling",
                (int(pts[0][0]), int(pts[0][1]) - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 180, 220), 1,
            )
        for line in self.camera.lines:
            ax, ay, bx, by = resolve_line(line, width, height)
            cv2.line(image, (int(ax), int(ay)), (int(bx), int(by)), (0, 200, 255), 2)
            cv2.putText(
                image, line.id, (int(ax), int(ay) - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1,
            )
        cv2.putText(
            image, f"occupancy: {self._occupancy}", (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2,
        )
