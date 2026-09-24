"""Fleet management — pure logic for a cloud-driven device fleet.

CamAI edge boxes are outbound-only: they never accept an inbound connection, so
the cloud cannot "push" to them. Instead the cloud holds the *desired state* for
each device (its config and the agent/model version it should be running) and the
edge *pulls* that state on its normal outbound channel. This module is the pure,
store-agnostic brain behind that model:

* **Health / alerting** — turn a stream of heartbeats into a list of actionable
  alerts (a device that stopped calling home, a camera whose FPS collapsed). This
  is what keeps per-camera billing operationally sane: a dead stream fires an
  alert instead of silently under-counting until someone visits the site.
* **Desired state** — the ``DesiredConfig`` / ``DesiredRelease`` a device should
  converge to. Kept deliberately additive (all overrides optional) so an operator
  can nudge one knob without re-specifying a whole site.
* **Canary rollout** — a *deterministic* helper that picks which devices get a new
  version first. Determinism matters: the same devices must stay in the canary set
  across repeated calls and across processes, or a rollout would reshuffle its
  blast radius every poll.

Everything here is pure (no DB, no network, no clock except what you pass in) so
it unit-tests against plain dicts and is trivially reusable by the router and by
the nightly jobs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Desired state
# --------------------------------------------------------------------------- #

class ReleaseChannel(str, Enum):
    """Which rollout track a device follows.

    ``canary`` devices receive a new release first so a bad build is caught on a
    small blast radius before it reaches the ``stable`` majority.
    """

    stable = "stable"
    canary = "canary"
    beta = "beta"


class DesiredConfig(BaseModel):
    """The config the cloud wants a device to be running.

    Intentionally all-optional overrides layered on top of the device's local
    ``SiteConfig`` — the operator sets only what they want to change, and the edge
    merges it in. ``config_version`` is a monotonically increasing integer the edge
    compares against what it last applied, so a pull that returns an unchanged
    version is a cheap no-op (no reconfigure, no dropped frames).
    """

    config_version: int = 0

    # Per-camera runtime overrides. ``None`` means "leave the local value alone".
    target_fps: Optional[float] = Field(default=None, gt=0)
    min_confidence: Optional[float] = Field(default=None, ge=0, le=1)

    # Operator can remotely disable a misbehaving camera without a site visit.
    disabled_cameras: list[str] = Field(default_factory=list)

    # Expected FPS per camera_id. Used purely by health detection to decide a
    # stream is "degraded"; kept in desired-config because only the operator who
    # provisioned the camera knows what "healthy" looks like for it.
    expected_fps: dict[str, float] = Field(default_factory=dict)

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: Optional[str] = None


class DesiredRelease(BaseModel):
    """The agent (and optionally model) version a device should be pinned to.

    Version *pinning* is what makes fleet upgrades safe: nothing auto-updates until
    the cloud says so, and a rollback is just re-pinning the previous version.
    """

    agent_version: str
    model_version: Optional[str] = Field(
        default=None,
        description="Identifier of the detector weights the device should run "
        "(e.g. 'yolo11n-retail-v3'); None => keep whatever it ships with.",
    )
    channel: ReleaseChannel = ReleaseChannel.stable
    notes: Optional[str] = None
    rollout_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #

class AlertKind(str, Enum):
    offline = "offline"                 # device stopped sending heartbeats entirely
    stale = "stale"                     # heartbeats are late but not yet "offline"
    degraded_stream = "degraded_stream"  # a camera's FPS is well below expected
    dark_stream = "dark_stream"          # a camera reports 0 FPS (feed is down)


class Severity(str, Enum):
    warning = "warning"
    critical = "critical"


class DeviceAlert(BaseModel):
    """One actionable problem with one device (and optionally one of its cameras)."""

    tenant_id: str
    device_id: str
    kind: AlertKind
    severity: Severity
    message: str

    camera_id: Optional[str] = None
    observed_fps: Optional[float] = None
    expected_fps: Optional[float] = None

    last_seen: Optional[datetime] = None
    age_seconds: Optional[float] = None


# --------------------------------------------------------------------------- #
# Heartbeat accessors (heartbeats arrive as dicts from the store or as models)
# --------------------------------------------------------------------------- #

def _get(hb: Any, key: str, default: Any = None) -> Any:
    """Read a field from a heartbeat that may be a dict or a pydantic model."""
    if isinstance(hb, Mapping):
        return hb.get(key, default)
    return getattr(hb, key, default)


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a heartbeat timestamp that may be an ISO string or a datetime.

    Always returns a timezone-aware UTC datetime (or None) so age arithmetic is
    never tripped up by a naive/aware mismatch.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Health detection
# --------------------------------------------------------------------------- #

def detect_stale_devices(
    heartbeats: Iterable[Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: float = 120.0,
    offline_after_seconds: float = 600.0,
) -> list[DeviceAlert]:
    """Flag devices whose last heartbeat is older than the given thresholds.

    Two thresholds instead of one so an operator sees a *warning* (a box that is
    merely late — often a transient network blip) before the *critical* page for a
    box that is genuinely gone. A heartbeat with no parseable timestamp is treated
    as offline: an unreadable heartbeat is not a healthy one.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    alerts: list[DeviceAlert] = []
    for hb in heartbeats:
        last_seen = _parse_ts(_get(hb, "ts"))
        device_id = _get(hb, "device_id", "unknown")
        tenant_id = _get(hb, "tenant_id", "unknown")
        age = None if last_seen is None else (now - last_seen).total_seconds()

        if age is None or age >= offline_after_seconds:
            alerts.append(DeviceAlert(
                tenant_id=tenant_id, device_id=device_id,
                kind=AlertKind.offline, severity=Severity.critical,
                message=(
                    "device offline: no heartbeat"
                    if age is None
                    else f"device offline: last heartbeat {age:.0f}s ago"
                ),
                last_seen=last_seen, age_seconds=age,
            ))
        elif age >= stale_after_seconds:
            alerts.append(DeviceAlert(
                tenant_id=tenant_id, device_id=device_id,
                kind=AlertKind.stale, severity=Severity.warning,
                message=f"heartbeats are late: last seen {age:.0f}s ago",
                last_seen=last_seen, age_seconds=age,
            ))
    return alerts


def detect_degraded_streams(
    heartbeats: Iterable[Any],
    *,
    expected_fps: Mapping[str, Mapping[str, float]] | None = None,
    default_expected_fps: float | None = None,
    min_fps_ratio: float = 0.5,
    now: datetime | None = None,
    fresh_within_seconds: float = 600.0,
) -> list[DeviceAlert]:
    """Flag cameras whose measured FPS has collapsed relative to what's expected.

    ``expected_fps`` maps device_id -> {camera_id -> expected fps} (typically taken
    from each device's ``DesiredConfig.expected_fps``); ``default_expected_fps`` is
    the fallback when a camera has no per-camera expectation. A stream at 0 FPS is a
    *dark* stream (feed down) and always critical; a stream below
    ``min_fps_ratio`` of expected is *degraded*.

    Only *fresh* heartbeats are inspected — an offline device's last-known FPS is
    stale and would double-report (it's already covered by the offline alert).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected_fps = expected_fps or {}
    alerts: list[DeviceAlert] = []

    for hb in heartbeats:
        last_seen = _parse_ts(_get(hb, "ts"))
        if last_seen is not None and (now - last_seen).total_seconds() > fresh_within_seconds:
            continue  # too old to trust these FPS numbers

        device_id = _get(hb, "device_id", "unknown")
        tenant_id = _get(hb, "tenant_id", "unknown")
        streams = _get(hb, "stream_fps", {}) or {}
        per_cam = expected_fps.get(device_id, {})

        for camera_id, fps in streams.items():
            expected = per_cam.get(camera_id, default_expected_fps)
            if expected is None or expected <= 0:
                continue  # no expectation configured -> nothing to compare against

            if fps <= 0:
                alerts.append(DeviceAlert(
                    tenant_id=tenant_id, device_id=device_id,
                    kind=AlertKind.dark_stream, severity=Severity.critical,
                    message=f"camera {camera_id} is dark (0 fps, expected ~{expected:g})",
                    camera_id=camera_id, observed_fps=fps, expected_fps=expected,
                    last_seen=last_seen,
                ))
            elif fps < expected * min_fps_ratio:
                alerts.append(DeviceAlert(
                    tenant_id=tenant_id, device_id=device_id,
                    kind=AlertKind.degraded_stream, severity=Severity.warning,
                    message=(
                        f"camera {camera_id} degraded: {fps:g} fps "
                        f"(< {min_fps_ratio:g}x expected {expected:g})"
                    ),
                    camera_id=camera_id, observed_fps=fps, expected_fps=expected,
                    last_seen=last_seen,
                ))
    return alerts


def compute_alerts(
    heartbeats: Iterable[Any],
    *,
    configs: Mapping[str, DesiredConfig] | None = None,
    default_expected_fps: float | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = 120.0,
    offline_after_seconds: float = 600.0,
    min_fps_ratio: float = 0.5,
) -> list[DeviceAlert]:
    """Full alert list for a fleet: staleness + degraded streams, sorted worst-first.

    ``configs`` (device_id -> DesiredConfig) supplies per-camera expected FPS. The
    heartbeats are materialized once so the two detectors can each iterate them.
    """
    heartbeats = list(heartbeats)
    expected = {
        dev_id: cfg.expected_fps
        for dev_id, cfg in (configs or {}).items()
        if cfg.expected_fps
    }

    alerts = detect_stale_devices(
        heartbeats, now=now,
        stale_after_seconds=stale_after_seconds,
        offline_after_seconds=offline_after_seconds,
    )
    alerts += detect_degraded_streams(
        heartbeats,
        expected_fps=expected,
        default_expected_fps=default_expected_fps,
        min_fps_ratio=min_fps_ratio,
        now=now,
        fresh_within_seconds=offline_after_seconds,
    )

    # Critical before warning, then by staleness, so the dashboard shows the fires
    # first without the caller needing to re-sort.
    severity_rank = {Severity.critical: 0, Severity.warning: 1}
    alerts.sort(key=lambda a: (severity_rank[a.severity], -(a.age_seconds or 0.0)))
    return alerts


# --------------------------------------------------------------------------- #
# Canary rollout
# --------------------------------------------------------------------------- #

def _canary_score(device_id: str, salt: str) -> int:
    """Stable pseudo-random score in [0, 2**32) for a device.

    Uses a content hash (not Python's salted ``hash()``) so the ordering is
    identical across processes and restarts — the property that makes a canary set
    stable rather than reshuffling on every rollout.
    """
    digest = hashlib.sha256(f"{salt}:{device_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def select_canary(
    device_ids: Iterable[str],
    *,
    fraction: float | None = None,
    count: int | None = None,
    salt: str = "camai-canary",
) -> list[str]:
    """Deterministically pick the canary subset of ``device_ids``.

    Give either a ``fraction`` (0..1, rounded up to at least one device when the
    fleet is non-empty and the fraction is > 0) or an explicit ``count``. Selection
    is by stable hash order, so growing the fleet or re-running the picker keeps the
    existing canary devices in the canary set instead of shuffling the blast radius.
    Returns device ids sorted for stable, readable output.
    """
    ids = sorted(set(device_ids))
    if not ids:
        return []

    if count is None:
        if fraction is None:
            raise ValueError("select_canary requires either fraction or count")
        if fraction <= 0:
            return []
        import math
        count = max(1, math.ceil(fraction * len(ids)))
    count = max(0, min(count, len(ids)))

    chosen = sorted(ids, key=lambda d: _canary_score(d, salt))[:count]
    return sorted(chosen)


def resolve_release(
    device_id: str,
    *,
    canary_devices: Iterable[str],
    canary_release: DesiredRelease,
    stable_release: DesiredRelease,
) -> DesiredRelease:
    """Pick the release a device should run given the current canary set.

    Small but load-bearing: it's the single place that decides "is this box on the
    new build yet?", so both the API and any rollout job agree.
    """
    return canary_release if device_id in set(canary_devices) else stable_release
