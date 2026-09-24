"""Edge FleetClient tests.

Drive the client against an in-memory httpx MockTransport (no network, no cloud):
verify it pulls + applies config only on a version advance, handles a 404 release
gracefully, acts on a changed pin exactly once, and reports its running version.
"""

import httpx

from camai_edge.config import CloudConfig
from camai_edge.fleet import FleetClient, FleetConfig


def _make_client(handler, *, on_config=None, on_release=None):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://cloud.example")
    return FleetClient(
        FleetConfig(enabled=True, base_url="https://cloud.example"),
        CloudConfig(),
        device_id="d1",
        tenant_id="t1",
        agent_version="1.0.0",
        model_version="yolo11n-v1",
        on_config=on_config,
        on_release=on_release,
        client=http,
    )


def test_fleet_config_defaults():
    fc = FleetConfig()
    assert fc.enabled is False
    assert fc.base_url == ""
    assert fc.poll_interval_seconds == 60.0


def test_disabled_client_is_inert():
    # No base_url and disabled -> builds no client, start() is a no-op.
    fc = FleetClient(
        FleetConfig(enabled=False),
        CloudConfig(),
        device_id="d1", tenant_id="t1", agent_version="1.0.0",
    )
    assert fc._client is None
    fc.start()  # must not raise
    fc.stop()


def test_config_applied_only_on_version_advance():
    state = {"config_version": 1, "target_fps": 6.0}
    applied = []
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json=state)
        return httpx.Response(404)  # no release pinned

    client = _make_client(handler, on_config=lambda c: applied.append(c))

    client.poll_once()
    assert len(applied) == 1
    assert applied[0]["target_fps"] == 6.0
    assert client.applied_config_version == 1

    # Same version on the next poll -> no re-apply (a reconfigure would drop frames).
    client.poll_once()
    assert len(applied) == 1

    # Bump the version -> applied again.
    state["config_version"] = 2
    state["target_fps"] = 4.0
    client.poll_once()
    assert len(applied) == 2
    assert applied[1]["target_fps"] == 4.0

    # It reported the version it is actually running.
    assert seen_headers.get("x-agent-version") == "1.0.0"
    assert seen_headers.get("x-model-version") == "yolo11n-v1"


def test_release_handler_fires_once_per_change():
    releases = []
    pin = {"agent_version": "1.1.0", "channel": "canary"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={"config_version": 0})
        return httpx.Response(200, json=pin)

    client = _make_client(handler, on_release=lambda r: releases.append(r))

    client.poll_once()
    client.poll_once()  # unchanged pin -> no second call
    assert len(releases) == 1
    assert releases[0]["agent_version"] == "1.1.0"

    pin["agent_version"] = "1.2.0"
    client.poll_once()
    assert len(releases) == 2


def test_missing_release_is_tolerated():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={"config_version": 0})
        return httpx.Response(404)

    fired = []
    client = _make_client(handler, on_release=lambda r: fired.append(r))
    result = client.poll_once()
    assert result["release"] is None
    assert fired == []  # a 404 must not trigger a release handler
