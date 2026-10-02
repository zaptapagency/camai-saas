"""Forklift ↔ pedestrian proximity — Tier-2 near-miss safety.

In a warehouse the most dangerous thing that happens routinely is a person and a
moving forklift getting too close. This mode raises a ``proximity_alert`` the
moment a tracked person comes within the danger distance of any forklift, and
carries how close they got in the event ``labels`` (``["dist:0.12"]`` — the
person↔forklift gap as a fraction of the frame diagonal, so it's resolution-
independent). It also emits a periodic ``occupancy_sample`` of how many people are
around the equipment, the baseline exposure the alerts sit on top of.

The danger distance is ``camera.proximity_threshold`` (a fraction of the frame
diagonal): a person is "near" a forklift when the gap between the person's
foot-point and the forklift is below that. No zones are needed — the whole frame
is the hazard area wherever a forklift appears.

Alerts are debounced per person track so a sustained close pass fires once, not
every frame: one event on the clear→near transition, then nothing while they stay
near. A person clear of all forklifts for ``_CLEAR_GRACE_SECONDS`` re-arms, so a
later approach fires again. Track state for people gone past a TTL is forgotten to
bound memory over long runs.
"""

from __future__ import annotations

from math import hypot

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection

_SAMPLE_INTERVAL = 15.0        # seconds — cadence of the people-around-equipment sample
_CLEAR_GRACE_SECONDS = 3.0     # clear of all forklifts this long ⇒ re-arm the alert
_TRACK_TTL_SECONDS = 60.0      # forget proximity state for tracks gone this long


class ProximityCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # track_id -> committed "is near a forklift" state (debounces the alert).
        self._near: dict[int, bool] = {}
        # track_id -> last time this track was near a forklift (drives the re-arm grace).
        self._last_near_ts: dict[int, float] = {}
        # track_id -> last time this track was seen at all (drives the prune TTL).
        self._last_seen_ts: dict[int, float] = {}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        diag = hypot(width, height) or 1.0
        threshold_px = self.camera.proximity_threshold * diag
        events: list[Event] = []

        people = [d for d in detections
                  if d.object_class == ObjectClass.person and d.track_id is not None]
        forklifts = [d for d in detections if d.object_class == ObjectClass.forklift]

        for p in people:
            tid = p.track_id
            self._last_seen_ts[tid] = ts
            px, py = p.foot_point

            near_forklifts = []
            min_dist = None
            for f in forklifts:
                fx, fy = f.foot_point
                dist = hypot(px - fx, py - fy)
                if dist < threshold_px:
                    near_forklifts.append(f)
                    if min_dist is None or dist < min_dist:
                        min_dist = dist

            near_now = bool(near_forklifts)
            committed = self._near.get(tid, False)

            if near_now:
                self._last_near_ts[tid] = ts
                if not committed:
                    # clear → near transition: raise one alert.
                    self._near[tid] = True
                    min_frac = min_dist / diag
                    events.append(self._event(
                        ts, EventType.proximity_alert, zone_id=None,
                        object_class=ObjectClass.person, track_id=tid,
                        count=len(near_forklifts), labels=[f"dist:{min_frac:.2f}"],
                    ))
                # else: still near — debounced, no new alert.
            elif committed:
                # Clear of all forklifts; re-arm only once the grace has elapsed.
                if ts - self._last_near_ts.get(tid, ts) >= _CLEAR_GRACE_SECONDS:
                    self._near[tid] = False

        # Periodic baseline: how many people are around the equipment.
        if ts - self._last_sample_ts >= _SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            events.append(self._event(ts, EventType.occupancy_sample,
                                      zone_id=None, count=len(people)))

        self._prune(ts)
        return events

    def _prune(self, ts: float) -> None:
        """Bound state over long runs: forget tracks not seen recently."""
        stale = [tid for tid, seen in self._last_seen_ts.items()
                 if ts - seen > _TRACK_TTL_SECONDS]
        for tid in stale:
            self._near.pop(tid, None)
            self._last_near_ts.pop(tid, None)
            self._last_seen_ts.pop(tid, None)

    def draw(self, image, frame_size):  # pragma: no cover
        return None
