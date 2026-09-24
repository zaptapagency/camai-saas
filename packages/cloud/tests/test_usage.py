"""Tests for the usage endpoint and the active-cameras tile in summary."""

import os
import tempfile

from fastapi.testclient import TestClient

from camai_schema import Event, EventBatch, EventType, Mode

_JSON = {"content-type": "application/json"}


def _client():
    os.environ["CAMAI_STORE"] = "sqlite"
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "usage.db")
    import importlib
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app), main


def _batch(cams):
    events = [Event(tenant_id="t1", site_id="s1", camera_id=c,
                    type=EventType.occupancy_sample, mode=Mode.parking, count=1)
              for c in cams]
    return EventBatch(device_id="d1", tenant_id="t1", events=events)


def test_usage_endpoint_counts_active_cameras():
    client, main = _client()
    main.store.upsert_billing_account("t1", "cus_1", "si_1", "growth")
    client.post("/v1/ingest/events",
                content=_batch(["cam-a", "cam-b", "cam-a"]).model_dump_json(), headers=_JSON)

    u = client.get("/v1/tenants/t1/usage").json()
    assert u["active_cameras"] == 2
    assert sorted(u["camera_ids"]) == ["cam-a", "cam-b"]
    assert u["plan"] == "growth"
    assert u["period_start"] < u["period_end"]


def test_summary_includes_active_cameras_tile():
    client, _ = _client()
    client.post("/v1/ingest/events",
                content=_batch(["cam-x"]).model_dump_json(), headers=_JSON)
    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["active_cameras"] == 1
