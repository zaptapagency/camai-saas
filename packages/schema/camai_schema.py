"""CamAI event contract — the single source of truth for edge <-> cloud.

Both the edge agent and the cloud ingest API import these models so the wire
format can never drift between the two sides. Keep this module dependency-light
(only pydantic) so it can be vendored into either package.

Event design notes
------------------
* Every event is tenant- and device-scoped. The cloud never trusts the edge for
  ``tenant_id``; it is derived from the authenticated device certificate and the
  value here is only a cross-check.
* Events are append-only and idempotent: ``event_id`` is a client-generated UUID
  so a retried batch (after a connectivity drop) does not double-count.
* Raw video never appears here by design. The optional ``clip_ref`` is a pointer
  to an object-storage key for a short snapshot the customer opted into, never the
  video itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt


SCHEMA_VERSION = "1.0.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mode(str, Enum):
    """The vertical a camera is configured for. One pipeline, several modes."""

    retail = "retail"
    warehouse = "warehouse"
    parking = "parking"
    queue = "queue"    # queue length + per-person wait time (dwell)
    safety = "safety"  # PPE compliance in a required-equipment zone
    traffic = "traffic"  # smart-city: directional vehicle flow across a line
    staffing = "staffing"  # workstation coverage: is a work area staffed (anonymous)
    capacity = "capacity"    # Tier 1: live headcount vs a configured occupancy limit
    proximity = "proximity"  # Tier 2: forklift<->pedestrian near-miss (needs a forklift class)
    fire = "fire"            # Tier 3: fire/smoke detection (needs a fire/smoke-trained model)
    thermal = "thermal"      # Tier 4: thermal fever/overheat screening (needs a thermal camera)
    drive_thru = "drive_thru"        # Tier 1: vehicle service-time per lane (dwell)
    loitering = "loitering"          # Tier 1: prolonged person presence in a zone
    intrusion = "intrusion"          # Tier 1: person in a restricted / after-hours zone
    crowd_density = "crowd_density"  # Tier 1: crowd headcount vs a crush-risk threshold
    tailgating = "tailgating"        # Tier 1: multiple people through a secure line together


class EventType(str, Enum):
    """Typed events rolled up into hourly/daily aggregates in the cloud."""

    entry = "entry"                    # retail: someone crossed a line inbound
    exit = "exit"                      # retail: someone crossed a line outbound
    occupancy_sample = "occupancy_sample"  # periodic count-in-zone snapshot (all modes)
    vehicle_parked = "vehicle_parked"  # parking: a space transitioned to occupied
    vehicle_left = "vehicle_left"      # parking: a space transitioned to free
    count_delta = "count_delta"        # warehouse: net change of objects in a zone
    dwell = "dwell"                    # a tracked id left a zone; carries dwell seconds
    ppe_violation = "ppe_violation"    # safety: a person in a zone is missing required PPE
    vehicle_crossing = "vehicle_crossing"  # traffic: a vehicle crossed a line (direction in labels)
    capacity_breach = "capacity_breach"    # capacity: a zone crossed above its configured occupancy limit
    proximity_alert = "proximity_alert"    # proximity: a person and a forklift came within the danger distance
    hazard_alert = "hazard_alert"          # fire: fire/smoke appeared (hazard type in labels)
    overheat_alert = "overheat_alert"      # thermal: an object/person exceeded the temperature threshold
    loitering_alert = "loitering_alert"    # loitering: a person dwelled in a zone past the threshold
    intrusion_alert = "intrusion_alert"    # intrusion: a person entered a restricted/after-hours zone
    crowd_alert = "crowd_alert"            # crowd_density: headcount crossed the crush-risk threshold
    tailgating_alert = "tailgating_alert"  # tailgating: multiple people crossed a secure line together


class ObjectClass(str, Enum):
    person = "person"
    vehicle = "vehicle"
    # Finer vehicle types for traffic class-breakdown (optional; via a detector
    # class_map — plain COCO collapses these into `vehicle`).
    car = "car"
    truck = "truck"
    bus = "bus"
    motorcycle = "motorcycle"
    bicycle = "bicycle"
    forklift = "forklift"
    pallet = "pallet"
    # Hazard classes (from a fire/smoke-trained detector; COCO does not provide these).
    fire = "fire"
    smoke = "smoke"
    # PPE items (from a safety-trained detector; COCO does not provide these).
    helmet = "helmet"
    vest = "vest"
    no_helmet = "no_helmet"
    no_vest = "no_vest"
    other = "other"


class Event(BaseModel):
    """A single analytics event emitted by the edge agent.

    Only *counts and events* leave the customer's network — this model is the
    entire footprint of what CamAI stores about activity in a monitored space.
    """

    schema_version: str = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=_utcnow, description="UTC time of the event")

    tenant_id: str
    site_id: str
    camera_id: str

    type: EventType
    mode: Mode

    # Where in the scene this happened (a line id for retail, a zone/space id else).
    zone_id: Optional[str] = None
    line_id: Optional[str] = None

    object_class: Optional[ObjectClass] = None
    track_id: Optional[int] = Field(
        default=None,
        description="Persistent tracker id, unique within a stream session only. "
        "Never a persistent identity — no biometric/re-id across sessions.",
    )

    # Payload varies by type; kept as explicit optional fields rather than a blob
    # so Timescale/aggregation queries stay simple.
    count: Optional[int] = Field(default=None, description="occupancy_sample / count value")
    delta: Optional[int] = Field(default=None, description="count_delta signed change")
    dwell_seconds: Optional[float] = Field(default=None, description="dwell duration")

    clip_ref: Optional[str] = Field(
        default=None,
        description="Object-storage key for an opt-in short snapshot. Never raw video.",
    )
    labels: Optional[list[str]] = Field(
        default=None,
        description="Small free-form tags for an event, e.g. the missing PPE items "
        "on a ppe_violation (['helmet','vest']). Generic so future verticals can reuse it.",
    )

    model_config = ConfigDict(use_enum_values=True)


class EventBatch(BaseModel):
    """What the edge agent POSTs to the cloud ingest endpoint.

    Batched every 10-60s. ``batch_id`` makes the whole batch idempotent so the
    ingest side can dedupe a retried batch cheaply before touching per-event ids.
    """

    schema_version: str = SCHEMA_VERSION
    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    device_id: str
    tenant_id: str
    sent_at: datetime = Field(default_factory=_utcnow)
    events: list[Event]


class Heartbeat(BaseModel):
    """Device/stream health, sent on the same outbound channel as events.

    This is what makes per-camera billing operationally sane at scale: a missing
    heartbeat or a dropped stream surfaces on the dashboard and fires an alert
    instead of becoming a site visit.
    """

    schema_version: str = SCHEMA_VERSION
    device_id: str
    tenant_id: str
    ts: datetime = Field(default_factory=_utcnow)

    agent_version: str
    uptime_seconds: float

    cpu_percent: Optional[float] = None
    gpu_percent: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    disk_free_gb: Optional[float] = None

    # Per-camera stream health: camera_id -> measured FPS (0.0 == dark).
    stream_fps: dict[str, float] = Field(default_factory=dict)
    queued_events: NonNegativeInt = 0


class IngestAck(BaseModel):
    """Cloud -> edge response. Lets the edge drop only what was durably accepted."""

    batch_id: str
    accepted: int
    duplicates: int = 0
    rejected: int = 0
