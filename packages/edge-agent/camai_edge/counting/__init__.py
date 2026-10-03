"""Counter factory — maps a camera's mode to its counting strategy.

One pipeline, three modes: the rest of the edge agent is mode-agnostic and just
asks here for the right counter.
"""

from __future__ import annotations

from camai_schema import Mode

from camai_edge.config import CameraConfig
from camai_edge.counting.abandoned_object import AbandonedObjectCounter
from camai_edge.counting.base import BaseCounter
from camai_edge.counting.capacity import CapacityCounter
from camai_edge.counting.crowd_density import CrowdDensityCounter
from camai_edge.counting.drive_thru import DriveThruCounter
from camai_edge.counting.fall import FallCounter
from camai_edge.counting.fire import FireCounter
from camai_edge.counting.intrusion import IntrusionCounter
from camai_edge.counting.loitering import LoiteringCounter
from camai_edge.counting.tailgating import TailgatingCounter
from camai_edge.counting.weapon import WeaponCounter
from camai_edge.counting.wrong_way import WrongWayCounter
from camai_edge.counting.line_crossing import LineCrossingCounter
from camai_edge.counting.proximity import ProximityCounter
from camai_edge.counting.queue import QueueCounter
from camai_edge.counting.safety import SafetyCounter
from camai_edge.counting.staffing import StaffingCounter
from camai_edge.counting.thermal import ThermalCounter
from camai_edge.counting.traffic import TrafficCounter
from camai_edge.counting.zone_occupancy import ZoneOccupancyCounter


def make_counter(tenant_id: str, site_id: str, camera: CameraConfig) -> BaseCounter:
    mode = Mode(camera.mode)
    if mode == Mode.retail:
        return LineCrossingCounter(tenant_id, site_id, camera)
    if mode in (Mode.parking, Mode.warehouse):
        return ZoneOccupancyCounter(tenant_id, site_id, camera)
    if mode == Mode.queue:
        return QueueCounter(tenant_id, site_id, camera)
    if mode == Mode.safety:
        return SafetyCounter(tenant_id, site_id, camera)
    if mode == Mode.traffic:
        return TrafficCounter(tenant_id, site_id, camera)
    if mode == Mode.staffing:
        return StaffingCounter(tenant_id, site_id, camera)
    if mode == Mode.capacity:
        return CapacityCounter(tenant_id, site_id, camera)
    if mode == Mode.proximity:
        return ProximityCounter(tenant_id, site_id, camera)
    if mode == Mode.fire:
        return FireCounter(tenant_id, site_id, camera)
    if mode == Mode.thermal:
        return ThermalCounter(tenant_id, site_id, camera)
    if mode == Mode.drive_thru:
        return DriveThruCounter(tenant_id, site_id, camera)
    if mode == Mode.loitering:
        return LoiteringCounter(tenant_id, site_id, camera)
    if mode == Mode.intrusion:
        return IntrusionCounter(tenant_id, site_id, camera)
    if mode == Mode.crowd_density:
        return CrowdDensityCounter(tenant_id, site_id, camera)
    if mode == Mode.tailgating:
        return TailgatingCounter(tenant_id, site_id, camera)
    if mode == Mode.fall:
        return FallCounter(tenant_id, site_id, camera)
    if mode == Mode.weapon:
        return WeaponCounter(tenant_id, site_id, camera)
    if mode == Mode.abandoned_object:
        return AbandonedObjectCounter(tenant_id, site_id, camera)
    if mode == Mode.wrong_way:
        return WrongWayCounter(tenant_id, site_id, camera)
    raise ValueError(f"unsupported mode: {mode}")


__all__ = ["make_counter", "BaseCounter"]
