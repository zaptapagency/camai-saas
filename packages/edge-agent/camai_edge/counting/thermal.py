"""Thermal fever / equipment-overheat screening (Tier 4).

Requires a **thermal camera**: each detection carries a per-object surface
temperature in Celsius (``Detection.temperature``). A person walking past an
entry kiosk, or a machine on a line, that reads at or above the camera's
``temp_threshold_c`` is flagged with an ``overheat_alert`` — the temperature
rides in the event ``labels`` (e.g. ``["39.2C"]``) so the cloud can show the
reading without a schema change.

Fever screening has to be *immediate*: unlike arrival/dwell counting there is no
grace before the first alert — a hot reading fires on the frame it appears. To
avoid re-alerting on every frame while the same track stays hot, an alert is
debounced per ``track_id``: one event on the normal→elevated transition, and no
more until the track re-arms. A track re-arms once it reads below threshold again
or has been unseen past a short grace window, so a genuinely new elevation (the
same person warms up again, or a new person reusing an id) re-fires.

A periodic ``occupancy_sample`` carries the count of objects reading elevated
*right now* — the baseline for a "N people flagged" live tile — independent of
the per-track alert debounce.

Detections with no temperature (an ordinary RGB detector, or an object the
thermal sensor could not read) are ignored entirely: never alerted, never
counted. When the camera has zones configured, only objects standing inside a
zone are in scope (the alert/sample remember which zone); with no zones the whole
frame is in scope and ``zone_id`` is ``None``.
"""

from __future__ import annotations

from typing import Optional

from camai_schema import Event, EventType

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_SAMPLE_INTERVAL = 15.0        # seconds — cadence of the "elevated right now" tile sample
_CLEAR_GRACE_SECONDS = 5.0     # below threshold or unseen this long ⇒ re-arm the track
_TRACK_TTL_SECONDS = 60.0      # forget per-track state for tracks gone this long


class ThermalCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # track_id -> {"committed": bool, "last_seen": ts, "last_elevated": ts}
        self._state: dict[int, dict] = {}
        self._last_sample_ts = 0.0

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        threshold = self.camera.temp_threshold_c
        events: list[Event] = []

        # In-scope candidates: a temperature reading, and (if zones are configured)
        # standing inside one. Carry the resolved zone id for the event payload.
        candidates: list[tuple[Detection, Optional[str]]] = []
        for det in detections:
            if det.temperature is None:
                continue
            in_scope, zone_id = self._scope(det, width, height)
            if in_scope:
                candidates.append((det, zone_id))

        elevated_now = 0
        for det, zone_id in candidates:
            is_elevated = det.temperature >= threshold
            if is_elevated:
                elevated_now += 1

            # Alerts are per-track; anonymous (untracked) readings still count for
            # the sample above but cannot be debounced, so they raise no alert.
            if det.track_id is None:
                continue

            st = self._state.setdefault(
                det.track_id, {"committed": False, "last_seen": ts, "last_elevated": -1e9}
            )
            st["last_seen"] = ts
            if is_elevated:
                st["last_elevated"] = ts
                if not st["committed"]:
                    st["committed"] = True
                    events.append(self._event(
                        ts, EventType.overheat_alert, zone_id=zone_id,
                        object_class=det.object_class, track_id=det.track_id,
                        count=1, labels=[f"{det.temperature:.1f}C"],
                    ))
            else:
                # Read below threshold ⇒ re-arm so a later elevation re-fires.
                st["committed"] = False

        self._sweep(ts)

        # Periodic baseline: how many objects read elevated this frame.
        if ts - self._last_sample_ts >= _SAMPLE_INTERVAL:
            self._last_sample_ts = ts
            events.append(self._event(ts, EventType.occupancy_sample,
                                      zone_id=None, count=elevated_now, labels=None))
        return events

    def _scope(
        self, det: Detection, width: int, height: int
    ) -> tuple[bool, Optional[str]]:
        """Is this detection in scope, and which zone is it in?

        With no zones configured the whole frame is in scope (``zone_id`` None).
        With zones, only a detection whose foot point falls inside one is in scope.
        """
        if not self.camera.zones:
            return True, None
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            if point_in_polygon(*det.foot_point, poly):
                return True, zone.id
        return False, None

    def _sweep(self, ts: float) -> None:
        """Re-arm tracks unseen past the grace window; prune long-gone tracks."""
        stale: list[int] = []
        for tid, st in self._state.items():
            unseen = ts - st["last_seen"]
            if unseen > _TRACK_TTL_SECONDS:
                stale.append(tid)
            elif unseen >= _CLEAR_GRACE_SECONDS:
                st["committed"] = False
        for tid in stale:
            del self._state[tid]

    def draw(self, image, frame_size):  # pragma: no cover
        import cv2
        import numpy as np

        width, height = frame_size
        for zone in self.camera.zones:
            poly = resolve_zone(zone, width, height)
            pts = np.array([[int(x), int(y)] for x, y in poly], dtype=np.int32)
            cv2.polylines(image, [pts], True, (0, 0, 255), 2)
            cv2.putText(image, f"{zone.id}: thermal >= {self.camera.temp_threshold_c:.0f}C",
                        (int(pts[0][0]), int(pts[0][1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
