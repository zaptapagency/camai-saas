"""Tier-3 fire/smoke hazard detection.

Fed the detections from a fire/smoke-trained model, this raises a ``hazard_alert``
the moment a hazard appears in a monitored scope — carrying *which* hazard types
are present in the event's ``labels`` (e.g. ``["fire", "smoke"]``) and how many
hazard detections are in that scope in ``count``.

**Requires a fire/smoke-trained detector.** COCO (the default model) has no fire or
smoke classes, so this mode only produces signal when the camera's
``detector.classes`` maps a hazard model's class ids onto ``fire``/``smoke``. See
``DetectorConfig.classes``.

Scope is per-zone when the camera has zones (a hazard belongs to a zone when its
centroid falls inside that zone), else a single scene scope. Alerts are debounced
per scope: one event when a scope first goes hazardous, and again only if the *set*
of hazard types changes (smoke → smoke+fire) — mirroring how the PPE counter
re-fires when the missing-item set changes. A scope that stays clear for the grace
period re-arms, so a fresh hazard fires again; a brief single-frame dropout inside
the grace does not re-arm.
"""

from __future__ import annotations

from camai_schema import Event, EventType, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.base import BaseCounter
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

_CLEAR_GRACE_SECONDS = 5.0  # a scope clear this long re-arms so a new hazard re-fires

# Hazard classes a fire/smoke-trained detector may emit.
_HAZARD = {ObjectClass.fire, ObjectClass.smoke}


class FireCounter(BaseCounter):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        super().__init__(tenant_id, site_id, camera)
        # scope key (zone id, or None for scene) -> committed set of alerted hazard
        # types (empty set == clear) and the ts a still-clear scope has been clear
        # since (None while hazardous).
        self._committed: dict[str | None, set[str]] = {}
        self._clear_since: dict[str | None, float | None] = {}

    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        width, height = frame_size
        events: list[Event] = []

        hazards = [d for d in detections if d.object_class in _HAZARD]

        for scope_id, poly in self._scopes(width, height):
            if poly is None:
                in_scope = hazards
            else:
                in_scope = [d for d in hazards if point_in_polygon(*d.centroid, poly)]

            types = {d.object_class.value for d in in_scope}
            committed = self._committed.get(scope_id, set())

            if types:
                # Hazard present: cancel any pending clear timer and (re-)fire when
                # the hazard-type set differs from what we last alerted on.
                self._clear_since[scope_id] = None
                if types != committed:
                    self._committed[scope_id] = set(types)
                    events.append(self._event(
                        ts, EventType.hazard_alert, zone_id=scope_id,
                        count=len(in_scope), labels=sorted(types),
                    ))
            else:
                # No hazard: run the clear-grace timer; re-arm once it elapses.
                since = self._clear_since.get(scope_id)
                if since is None:
                    self._clear_since[scope_id] = ts
                elif ts - since >= _CLEAR_GRACE_SECONDS:
                    self._committed[scope_id] = set()

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
            alerting = bool(self._committed.get(zone.id))
            color = (0, 0, 255) if alerting else (0, 200, 0)
            cv2.polylines(image, [pts], True, color, 2)
