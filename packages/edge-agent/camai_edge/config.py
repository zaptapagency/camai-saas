"""Edge-agent configuration model.

One YAML file per site describes everything an edge box needs: which tenant it
belongs to, which cameras to pull, and — per camera — the counting mode and the
zones/lines drawn during calibration. Calibration (drawing the geometry) is the
single biggest driver of real-world accuracy, so the geometry lives here in
config rather than being guessed by the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

# Vendored from packages/schema (installed via `pip install -e ../schema`).
from camai_schema import Mode

# Safe import: identity.py depends only on pydantic (no import cycle back to config).
from camai_edge.identity import DeviceIdentityConfig


class Point(BaseModel):
    """Normalized image coordinate in [0, 1] so geometry is resolution-independent."""

    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _in_unit_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("coordinates must be normalized to [0, 1]")
        return v


class Line(BaseModel):
    """A virtual line for retail entry/exit counting.

    ``a`` -> ``b`` defines the segment. The "inside" direction is the left-hand
    normal of a->b; a crossing left-to-right counts as ``entry``, the reverse as
    ``exit``. Flip ``invert`` if calibration shows the door is the other way.
    """

    id: str
    a: Point
    b: Point
    invert: bool = False


class Zone(BaseModel):
    """A polygon ROI: a parking space, an aisle, a checkout, or a warehouse bay."""

    id: str
    polygon: list[Point] = Field(min_length=3)
    capacity: Optional[int] = Field(
        default=None, description="For alerts, e.g. warehouse zone max occupancy."
    )
    required_ppe: list[str] = Field(
        default_factory=list,
        description="Safety mode: PPE items a person in this zone must wear, e.g. "
        "['helmet', 'vest']. A person missing any raises a ppe_violation.",
    )


class CameraConfig(BaseModel):
    id: str
    # RTSP url, a local file path, or an int (webcam index) as a string.
    source: str
    mode: Mode
    enabled: bool = True

    lines: list[Line] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)

    # Counting works fine well below full video rate; this caps inference cost so
    # one box can hold several streams.
    target_fps: float = Field(default=8.0, gt=0)

    # Only detections above this confidence are tracked/counted.
    min_confidence: float = Field(default=0.35, ge=0, le=1)

    # Proximity mode: a person and a forklift closer than this fraction of the frame
    # diagonal raise a near-miss alert.
    proximity_threshold: float = Field(default=0.15, gt=0, le=1)

    # Thermal mode: a detection whose measured temperature (°C) meets or exceeds this
    # raises an overheat alert.
    temp_threshold_c: float = Field(default=38.0)


class DetectorConfig(BaseModel):
    weights: str = "yolo11n.pt"  # nano baseline; swap for fine-tuned per-site weights
    device: str = "auto"          # "auto" | "cpu" | "cuda:0" ...
    imgsz: int = 640
    # Optional model-class-id -> ObjectClass-name map, overriding the default COCO
    # mapping. Required for safety mode: point `weights` at a PPE-trained model and
    # map its class ids, e.g. {0: "person", 1: "helmet", 2: "vest", 3: "no_helmet"}.
    classes: Optional[dict[int, str]] = None


class CloudConfig(BaseModel):
    # Outbound-only. Empty url => run fully offline (events print to console).
    ingest_url: str = ""
    device_cert: str = ""   # path to the per-device client cert (mTLS)
    device_key: str = ""
    ca_bundle: str = ""
    flush_interval_seconds: float = Field(default=15.0, gt=0)
    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)


class SiteConfig(BaseModel):
    tenant_id: str
    site_id: str
    device_id: str
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    cameras: list[CameraConfig] = Field(default_factory=list)
    # Per-device cryptographic identity (mTLS). Safe to import here — identity.py
    # depends only on pydantic, so there is no import cycle. Used by the agent at
    # startup via camai_edge.identity.ensure_identity(); when a cert is minted its
    # paths feed CloudConfig.device_cert/device_key and its device_id is authoritative.
    identity: "DeviceIdentityConfig" = Field(default_factory=lambda: DeviceIdentityConfig())

    # Fleet management (opt-in). Kept as primitives — NOT a FleetConfig — because
    # camai_edge.fleet imports config.CloudConfig, so importing FleetConfig here
    # would be a cycle. main.py maps these onto a FleetConfig at startup.
    fleet_enabled: bool = False
    fleet_poll_seconds: float = Field(default=60.0, gt=0)

    @classmethod
    def load(cls, path: str | Path) -> "SiteConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
