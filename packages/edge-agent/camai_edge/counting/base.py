"""Common base for the mode-specific counters.

A counter is fed the tracked detections for each frame and returns the events
that frame produced. The base carries the tenant/site/camera context so a counter
only has to build the type-specific payload.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from camai_schema import Event, EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.detect import Detection


class BaseCounter(ABC):
    def __init__(self, tenant_id: str, site_id: str, camera: CameraConfig) -> None:
        self.tenant_id = tenant_id
        self.site_id = site_id
        self.camera = camera

    def _event(self, ts: float, type_: EventType, **payload) -> Event:
        return Event(
            ts=datetime.fromtimestamp(ts, tz=timezone.utc),
            tenant_id=self.tenant_id,
            site_id=self.site_id,
            camera_id=self.camera.id,
            type=type_,
            mode=Mode(self.camera.mode),
            **payload,
        )

    @abstractmethod
    def update(
        self, detections: list[Detection], frame_size: tuple[int, int], ts: float
    ) -> list[Event]:
        """Consume one frame's detections; return any events produced."""

    # Optional: draw overlays for the --show debug window. Default no-op.
    def draw(self, image, frame_size: tuple[int, int]) -> None:  # pragma: no cover
        return None


def relevant_classes_for(mode: Mode) -> set[ObjectClass]:
    if mode == Mode.parking:
        return {ObjectClass.vehicle}
    if mode == Mode.warehouse:
        return {ObjectClass.person, ObjectClass.forklift, ObjectClass.pallet}
    return {ObjectClass.person}  # retail
