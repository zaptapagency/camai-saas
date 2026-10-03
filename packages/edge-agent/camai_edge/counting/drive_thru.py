"""Drive-thru counting: vehicle service time per lane, and live lane occupancy.

A drive-thru lane zone is a polygon over a service lane (order point, pickup
window, wait bay). This counter reports two things retail/parking don't:

* **lane occupancy** — the instantaneous number of vehicles whose foot point is
  inside the lane, emitted periodically as an ``occupancy_sample`` (the live tile);
* **service time** — when a tracked vehicle leaves the lane, a ``dwell`` event
  carries how long it was in the lane. Averaged in the cloud, that's the "average
  service time" a drive-thru operator actually cares about.

Membership is smoothed by a short grace period: a vehicle must be *unseen* for
``_DWELL_GRACE_SECONDS`` before it counts as having left, so a one-frame miss or a
brief occlusion (another vehicle, signage) doesn't end (and restart) its service
time. That keeps per-vehicle timings robust in a busy lane.

Only vehicles are counted.
"""

from __future__ import annotations

from dataclasses import dataclass

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_OCCUPANCY_SAMPLE_INTERVAL = 15.0  # seconds — cadence of the live lane-occupancy tile
_DWELL_GRACE_SECONDS = 5.0         # unseen-for longer than this ⇒ the vehicle left


@dataclass
class _Vehicle:
    """One vehicle's presence in a lane zone."""

    enter_ts: float
    last_seen_ts: float


class DriveThruCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # zone_id -> {track_id -> _Vehicle}
        self._in_lane: dict[str, dict[int, _Vehicle]] = {z.id: {} for z in camera.zones}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        vehicles = [
            d for d in detections
            if d.object_class == ObjectClass.vehicle and d.track_id is not None
        ]

        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            tracked = self._in_lane[zone.id]

            inside_now = {
                d.track_id for d in vehicles if point_in_polygon(*d.foot_point, poly)
            }

            # Update / register every vehicle currently in the lane.
            for tid in inside_now:
                v = tracked.get(tid)
                if v is None:
                    tracked[tid] = _Vehicle(enter_ts=ts, last_seen_ts=ts)
                else:
                    v.last_seen_ts = ts

            # Anyone unseen beyond the grace window has left → emit its service time.
            left = [
                tid for tid, v in tracked.items()
                if tid not in inside_now and (ts - v.last_seen_ts) > _DWELL_GRACE_SECONDS
            ]
            for tid in left:
                v = tracked.pop(tid)
                events.append(
                    self._event(
                        v.last_seen_ts, EventType.dwell,
                        zone_id=zone.id, object_class=ObjectClass.vehicle,
                        track_id=tid, dwell_seconds=round(v.last_seen_ts - v.enter_ts, 2),
                    )
                )

        # Periodic lane-occupancy sample (instantaneous vehicle count in each lane).
        if ts - self._last_sample_ts >= _OCCUPANCY_SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            for zone in self.camera.zones:
                count = self._current_count(zone.id, frame_size, vehicles)
                events.append(
                    self._event(ts, EventType.occupancy_sample,
                                zone_id=zone.id, count=count)
                )
        return events

    def _current_count(self, zone_id, frame_size, vehicles) -> int:
        """Vehicles physically in the lane this frame (not the grace-retained set)."""
        width, height = frame_size
        poly = resolve_zone(next(z for z in self.camera.zones if z.id == zone_id),
                            width, height)
        return sum(1 for d in vehicles if point_in_polygon(*d.foot_point, poly))

    def draw(self, image, frame_size):  # pragma: no cover
        return None
