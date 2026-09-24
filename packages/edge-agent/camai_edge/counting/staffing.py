"""Workstation coverage + activity — is a station staffed, and is it being worked?

For a restaurant (or any operation with fixed stations), each zone is a work area:
the prep line, the counter, the grill, the dishwash. This answers two operational
questions as an **anonymous, station-level** signal:

* **Coverage** — is the station manned right now, and how long has it been left
  unattended? (headcount per zone; 0 = unstaffed).
* **Activity** — when the station *is* manned, is someone actually working it, or
  just standing there? A station is ``active`` while a present worker is moving
  (measured as bounding-box motion — a cook plating, reaching, turning — not only
  walking) and flips to ``static`` once every present worker has been still past a
  grace window. The state rides in the event ``labels`` (``["active"]`` /
  ``["static"]``), so no schema change is needed.

It never identifies individuals or scores a *person's* productivity — activity is
per **station**, not per employee (CamAI does no biometric identification). That is
deliberate: station-level "the grill was worked 80% of the rush, the prep line sat
idle at 7pm" is the operational signal a manager needs, and it is what keeps this
use legal and sellable. "Employee #3 was idle 12 minutes" is out of scope by design.

Emissions per station zone:
* ``occupancy_sample`` with the current staff headcount (0 = unstaffed), sampled
  periodically for the live tile and trend, tagged with the activity state when
  staffed;
* a same-zone ``occupancy_sample`` fired immediately on any *state change* —
  staffed⇄unstaffed **or** active⇄static — so an "prep line manned but idle during
  the rush" alert is as timely as an "unmanned" one.

Grace periods keep both signals from flickering: a station only counts as unstaffed
after ``_UNSTAFFED_GRACE_SECONDS`` empty, and only as static after every present
worker has been still for ``_IDLE_GRACE_SECONDS``. Coverage % and active % over a
shift are derived cloud-side from the samples.
"""

from __future__ import annotations

from math import hypot

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0   # seconds — cadence of the staffed-headcount tile
_UNSTAFFED_GRACE_SECONDS = 10.0     # empty longer than this ⇒ the station is unstaffed
_IDLE_GRACE_SECONDS = 8.0           # every present worker still this long ⇒ static
_MOTION_THRESHOLD = 0.015           # bbox motion (fraction of frame diagonal / sec) = "moving"
_REAPPEAR_GAP_SECONDS = 1.5         # a track unseen this long, then back, counts as movement
_TRACK_TTL_SECONDS = 60.0           # forget motion state for tracks gone this long


class StaffingCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> committed "is staffed" state and the last time someone was in it
        self._staffed: dict[str, bool] = {z.id: False for z in camera.zones}
        # zone_id -> committed "is being actively worked" state (only meaningful staffed)
        self._active: dict[str, bool] = {z.id: False for z in camera.zones}
        # Far in the past so a station empty at startup is unstaffed, not "in grace".
        self._last_present_ts: dict[str, float] = {z.id: -1e9 for z in camera.zones}
        self._last_sample_ts = 0.0
        # track_id -> last (cx, cy, w, h, ts) box, for per-track motion.
        self._last_box: dict[int, tuple[float, float, float, float, float]] = {}
        # track_id -> last time this track moved (a fresh arrival counts as moving).
        self._last_active_ts: dict[int, float] = {}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        diag = hypot(width, height) or 1.0
        events: list[Event] = []

        people = [d for d in detections if d.object_class == ObjectClass.person]

        # 1) Update per-track motion (drives the active/static state below).
        self._update_motion(people, diag, ts)

        # 2) Per-zone coverage + activity, with an immediate sample on any change.
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            in_zone = [d for d in people if point_in_polygon(*d.foot_point, poly)]
            head = len(in_zone)

            if head > 0:
                self._last_present_ts[zone.id] = ts
                staffed_now = True
            else:
                staffed_now = (ts - self._last_present_ts[zone.id]) <= _UNSTAFFED_GRACE_SECONDS

            active_now = self._zone_active(zone.id, in_zone, staffed_now, ts)

            if staffed_now != self._staffed[zone.id] or active_now != self._active[zone.id]:
                self._staffed[zone.id] = staffed_now
                self._active[zone.id] = active_now
                events.append(self._event(ts, EventType.occupancy_sample, zone_id=zone.id,
                                          count=head, labels=self._labels(staffed_now, active_now)))

        # 3) Periodic sample per zone (headcount + current committed activity).
        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                poly = resolve_zone(zone, width, height)
                head = sum(1 for d in people if point_in_polygon(*d.foot_point, poly))
                events.append(self._event(ts, EventType.occupancy_sample, zone_id=zone.id,
                                          count=head,
                                          labels=self._labels(self._staffed[zone.id],
                                                              self._active[zone.id])))

        self._prune_tracks(ts)
        return events

    def _update_motion(self, people: list[Detection], diag: float, ts: float) -> None:
        """Mark each tracked person as having moved this frame, or not."""
        for d in people:
            tid = d.track_id
            if tid is None:
                continue
            cx, cy = d.centroid
            w, h = d.x2 - d.x1, d.y2 - d.y1
            prev = self._last_box.get(tid)
            if prev is None:
                # First sighting — a worker arriving at the station is "moving".
                self._last_active_ts[tid] = ts
            else:
                pcx, pcy, pw, ph, pts = prev
                dt = ts - pts
                if dt > _REAPPEAR_GAP_SECONDS:
                    # Was gone and came back → treat as movement, not a long still spell.
                    self._last_active_ts[tid] = ts
                elif dt > 0:
                    disp = abs(cx - pcx) + abs(cy - pcy) + abs(w - pw) + abs(h - ph)
                    if (disp / diag) / dt >= _MOTION_THRESHOLD:
                        self._last_active_ts[tid] = ts
            self._last_box[tid] = (cx, cy, w, h, ts)

    def _zone_active(
        self, zone_id: str, in_zone: list[Detection], staffed_now: bool, ts: float
    ) -> bool:
        """Is the station being actively worked?

        A staffed station is active while any *present, tracked* worker has moved
        within the idle-grace window. If it is staffed only by the unstaffed-grace
        (no one visible this frame) or only by untracked detections, we keep the
        prior activity state rather than flip it on missing evidence.
        """
        if not staffed_now:
            return False
        tracked = [d.track_id for d in in_zone if d.track_id is not None]
        if not tracked:
            return self._active[zone_id]
        return any(
            (ts - self._last_active_ts.get(tid, -1e9)) <= _IDLE_GRACE_SECONDS
            for tid in tracked
        )

    @staticmethod
    def _labels(staffed: bool, active: bool) -> list[str] | None:
        """Activity tag for a sample; ``None`` when unstaffed (count=0 says that)."""
        if not staffed:
            return None
        return ["active"] if active else ["static"]

    def _prune_tracks(self, ts: float) -> None:
        """Bound motion state over long runs: forget tracks not seen recently."""
        stale = [tid for tid, box in self._last_box.items() if ts - box[4] > _TRACK_TTL_SECONDS]
        for tid in stale:
            self._last_box.pop(tid, None)
            self._last_active_ts.pop(tid, None)

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            staffed = self._staffed[zone.id]
            active = self._active[zone.id]
            if not staffed:
                color, label = (0, 0, 255), "UNSTAFFED"
            elif active:
                color, label = (0, 180, 0), "active"
            else:
                color, label = (0, 180, 220), "STATIC"
            cv2.polylines(image, [pts], True, color, 2)
            cv2.putText(image, f"{zone.id}: {label}", (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
