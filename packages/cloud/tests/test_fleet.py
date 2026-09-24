"""Fleet management tests — pure logic + the HTTP surface.

Covers what a rollout and a night-time page depend on: staleness/degradation is
detected from heartbeats, the canary set is deterministic and correctly sized, and
desired config/release round-trips through the router (with the version counter
managed server-side).
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import fleet_router
from app.fleet import (
    AlertKind,
    DesiredConfig,
    Severity,
    compute_alerts,
    detect_stale_devices,
    resolve_release,
    select_canary,
)
from app.fleet import DesiredRelease  # noqa: E402


NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _hb(device_id, tenant_id="t1", age_seconds=0.0, stream_fps=None):
    """A heartbeat dict shaped exactly like Store.device_health() returns."""
    return {
        "device_id": device_id,
        "tenant_id": tenant_id,
        "ts": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "stream_fps": stream_fps or {},
    }


# --------------------------------------------------------------------------- #
# Pure: staleness / degradation
# --------------------------------------------------------------------------- #

def test_staleness_thresholds():
    hbs = [
        _hb("fresh", age_seconds=10),
        _hb("late", age_seconds=200),      # > stale (120), < offline (600)
        _hb("gone", age_seconds=1000),     # > offline
    ]
    alerts = detect_stale_devices(
        hbs, now=NOW, stale_after_seconds=120, offline_after_seconds=600
    )
    by_device = {a.device_id: a for a in alerts}
    assert "fresh" not in by_device
    assert by_device["late"].kind is AlertKind.stale
    assert by_device["late"].severity is Severity.warning
    assert by_device["gone"].kind is AlertKind.offline
    assert by_device["gone"].severity is Severity.critical


def test_unparseable_or_missing_ts_is_offline():
    alerts = detect_stale_devices(
        [{"device_id": "d1", "tenant_id": "t1", "ts": None}],
        now=NOW,
    )
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.offline


def test_degraded_and_dark_streams():
    configs = {
        "d1": DesiredConfig(expected_fps={"cam_a": 8.0, "cam_b": 8.0, "cam_c": 8.0}),
    }
    hbs = [_hb("d1", stream_fps={"cam_a": 7.5, "cam_b": 2.0, "cam_c": 0.0})]
    alerts = compute_alerts(hbs, configs=configs, now=NOW, min_fps_ratio=0.5)
    kinds = {a.camera_id: a.kind for a in alerts}
    assert "cam_a" not in kinds                       # healthy (7.5 of 8)
    assert kinds["cam_b"] is AlertKind.degraded_stream  # 2 < 0.5*8
    assert kinds["cam_c"] is AlertKind.dark_stream      # 0 fps

    # Critical (dark) sorts before warning (degraded).
    assert alerts[0].severity is Severity.critical


def test_no_expected_fps_means_no_degradation_alert():
    hbs = [_hb("d1", stream_fps={"cam_a": 0.0})]
    # No config, no default expectation -> nothing to compare against.
    assert compute_alerts(hbs, now=NOW) == []


# --------------------------------------------------------------------------- #
# Pure: canary selection
# --------------------------------------------------------------------------- #

def test_canary_is_deterministic_and_sized():
    devices = [f"dev-{i}" for i in range(10)]
    a = select_canary(devices, fraction=0.2)
    b = select_canary(devices, fraction=0.2)
    assert a == b                      # deterministic across calls
    assert len(a) == 2                 # ceil(0.2 * 10)
    assert set(a).issubset(devices)


def test_canary_selection_is_monotonic_in_count():
    # Growing the canary size only *adds* devices — it never drops one that was
    # already in the set. This is what lets an operator widen a rollout gradually
    # without churning the boxes that already took the new build.
    devices = [f"dev-{i}" for i in range(20)]
    prev = set()
    for count in range(0, len(devices) + 1):
        cur = set(select_canary(devices, count=count))
        assert prev.issubset(cur)
        assert len(cur) == count
        prev = cur


def test_canary_ordering_independent_of_input_order():
    # Selection depends only on device identity (stable hash), not on the order the
    # ids happen to arrive in, so two callers with the same fleet agree.
    a = select_canary(["x", "y", "z", "w"], count=2)
    b = select_canary(["w", "z", "y", "x"], count=2)
    assert a == b


def test_canary_fraction_zero_and_count_cap():
    devices = ["a", "b", "c"]
    assert select_canary(devices, fraction=0.0) == []
    assert select_canary([], fraction=0.5) == []
    assert len(select_canary(devices, count=99)) == 3  # capped at fleet size


def test_resolve_release_picks_canary_for_canary_devices():
    canary = select_canary(["a", "b", "c", "d"], fraction=0.5)
    stable = DesiredRelease(agent_version="1.0.0")
    new = DesiredRelease(agent_version="1.1.0")
    for d in ["a", "b", "c", "d"]:
        chosen = resolve_release(d, canary_devices=canary, canary_release=new, stable_release=stable)
        expected = "1.1.0" if d in canary else "1.0.0"
        assert chosen.agent_version == expected


# --------------------------------------------------------------------------- #
# HTTP: config/release round-trip + alerts via TestClient
# --------------------------------------------------------------------------- #

def _client(heartbeats=None):
    """Build a FastAPI app with just the fleet router, wired to a temp store and a
    synthetic heartbeat accessor — no dependency on app.main or a real event store.
    """
    db = os.path.join(tempfile.mkdtemp(), "fleet.db")
    store = fleet_router.FleetStore(db)
    hbs = heartbeats or []
    fleet_router.configure(
        store=store,
        heartbeat_accessor=lambda tenant_id: [h for h in hbs if h["tenant_id"] == tenant_id],
    )
    app = FastAPI()
    app.include_router(fleet_router.router)
    return TestClient(app)


def test_config_push_round_trip_and_version_bump():
    client = _client()

    # Fresh device: empty config, version 0, no 404.
    r0 = client.get("/v1/devices/d1/config")
    assert r0.status_code == 200
    assert r0.json()["config_version"] == 0

    # Operator sets config; server assigns version 1 (ignores any sent value).
    body = DesiredConfig(target_fps=6.0, config_version=999).model_dump(mode="json")
    r1 = client.put("/v1/devices/d1/config", params={"tenant_id": "t1"}, json=body)
    assert r1.status_code == 200
    assert r1.json()["config_version"] == 1
    assert r1.json()["target_fps"] == 6.0

    # Edge pulls it back.
    r2 = client.get("/v1/devices/d1/config")
    assert r2.json()["config_version"] == 1
    assert r2.json()["target_fps"] == 6.0

    # A second write bumps to version 2.
    r3 = client.put("/v1/devices/d1/config",
                    params={"tenant_id": "t1"},
                    json=DesiredConfig(min_confidence=0.5).model_dump(mode="json"))
    assert r3.json()["config_version"] == 2


def test_release_pin_round_trip_and_404_when_unset():
    client = _client()
    assert client.get("/v1/devices/d9/release").status_code == 404

    rel = DesiredRelease(agent_version="1.2.3", model_version="yolo11n-retail-v2").model_dump(mode="json")
    put = client.put("/v1/devices/d9/release", params={"tenant_id": "t1"}, json=rel)
    assert put.status_code == 200

    got = client.get("/v1/devices/d9/release")
    assert got.status_code == 200
    assert got.json()["agent_version"] == "1.2.3"
    assert got.json()["model_version"] == "yolo11n-retail-v2"


def _fresh_hb(device_id, tenant_id="t1", age_seconds=0.0, stream_fps=None):
    """Heartbeat timestamped relative to real wall-clock, for endpoint tests where
    the router computes staleness against ``datetime.now`` (no injectable clock)."""
    now = datetime.now(timezone.utc)
    return {
        "device_id": device_id,
        "tenant_id": tenant_id,
        "ts": (now - timedelta(seconds=age_seconds)).isoformat(),
        "stream_fps": stream_fps or {},
    }


def test_alerts_endpoint_derives_from_heartbeats():
    hbs = [
        _fresh_hb("healthy", age_seconds=5, stream_fps={"cam": 8.0}),
        _fresh_hb("gone", age_seconds=5000),
        _fresh_hb("degraded", age_seconds=5, stream_fps={"cam": 1.0}),
    ]
    client = _client(heartbeats=hbs)
    # Give the degraded device an expected-fps config so degradation is detectable.
    client.put("/v1/devices/degraded/config", params={"tenant_id": "t1"},
               json=DesiredConfig(expected_fps={"cam": 8.0}).model_dump(mode="json"))

    resp = client.get("/v1/tenants/t1/alerts")
    assert resp.status_code == 200
    body = resp.json()
    kinds = {(a["device_id"], a.get("camera_id")): a["kind"] for a in body["alerts"]}
    assert kinds[("gone", None)] == "offline"
    assert kinds[("degraded", "cam")] == "degraded_stream"
    assert not any(a["device_id"] == "healthy" for a in body["alerts"])


def test_canary_rollout_endpoint_pins_devices():
    hbs = [_hb(f"d{i}", age_seconds=5) for i in range(4)]
    client = _client(heartbeats=hbs)
    req = {
        "release": {"agent_version": "2.0.0"},
        "baseline": {"agent_version": "1.0.0"},
        "fraction": 0.5,
    }
    resp = client.put("/v1/tenants/t1/rollout", json=req)
    assert resp.status_code == 200
    out = resp.json()
    assert out["total_devices"] == 4
    assert out["canary_count"] == 2

    # Canary devices are pinned to the new version; the rest to baseline.
    for device_id in out["canary"]:
        got = client.get(f"/v1/devices/{device_id}/release").json()
        assert got["agent_version"] == "2.0.0"
        assert got["channel"] == "canary"
    baseline_devices = [d for d in [f"d{i}" for i in range(4)] if d not in out["canary"]]
    for device_id in baseline_devices:
        got = client.get(f"/v1/devices/{device_id}/release").json()
        assert got["agent_version"] == "1.0.0"

    # Re-running is idempotent: same canary set.
    resp2 = client.put("/v1/tenants/t1/rollout", json=req)
    assert resp2.json()["canary"] == out["canary"]
