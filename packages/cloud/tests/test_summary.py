"""Tests for the dashboard summary aggregation."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from camai_schema import Event, EventBatch, EventType, Mode, ObjectClass

_JSON = {"content-type": "application/json"}


def _client():
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    import importlib
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def _ev(**kw):
    base = dict(tenant_id="t1", site_id="s1", camera_id="c1")
    base.update(kw)
    return Event(**base)


def test_summary_totals_and_tiles():
    client = _client()
    events = [
        _ev(type=EventType.entry, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.entry, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.entry, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.exit, mode=Mode.retail, line_id="door"),
        _ev(type=EventType.occupancy_sample, mode=Mode.retail, count=2),
        _ev(camera_id="lot", type=EventType.vehicle_parked, mode=Mode.parking, zone_id="space-1"),
    ]
    batch = EventBatch(device_id="d1", tenant_id="t1", events=events)
    r = client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    assert r.json()["accepted"] == 6

    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["entries"] == 3
    assert s["totals"]["exits"] == 1
    assert s["totals"]["retail_occupancy"] == 2
    assert s["totals"]["parking_spaces_occupied"] == 1

    # occupancy tile present for camera c1
    assert any(o["count"] == 2 for o in s["occupancy"])
    # parking space shows parked
    assert any(p["zone_id"] == "space-1" and p["state"] == "parked" for p in s["parking"])


def test_summary_avg_wait_from_dwell_events():
    client = _client()
    events = [
        _ev(camera_id="till", type=EventType.dwell, mode=Mode.queue,
            zone_id="q1", dwell_seconds=30.0),
        _ev(camera_id="till", type=EventType.dwell, mode=Mode.queue,
            zone_id="q1", dwell_seconds=90.0),
    ]
    batch = EventBatch(device_id="d1", tenant_id="t1", events=events)
    client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)

    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["avg_wait_seconds"] == 60.0
    assert s["totals"]["wait_samples"] == 2


def test_summary_counts_ppe_violations_and_keeps_labels():
    client = _client()
    events = [
        _ev(camera_id="dock", type=EventType.ppe_violation, mode=Mode.safety,
            zone_id="hazard", track_id=1, count=1, labels=["vest"]),
        _ev(camera_id="dock", type=EventType.ppe_violation, mode=Mode.safety,
            zone_id="hazard", track_id=2, count=2, labels=["helmet", "vest"]),
    ]
    batch = EventBatch(device_id="d1", tenant_id="t1", events=events)
    client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)

    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["ppe_violations"] == 2
    # labels survive the round-trip in the recent feed (SQLite payload JSON)
    labelled = [e for e in s["recent"] if e.get("labels")]
    assert any(e["labels"] == ["helmet", "vest"] for e in labelled)


def test_summary_counts_vehicle_crossings():
    client = _client()
    events = [
        _ev(camera_id="road", type=EventType.vehicle_crossing, mode=Mode.traffic,
            line_id="cordon", object_class=ObjectClass.vehicle, track_id=1, count=1,
            labels=["forward"]),
        _ev(camera_id="road", type=EventType.vehicle_crossing, mode=Mode.traffic,
            line_id="cordon", object_class=ObjectClass.truck, track_id=2, count=1,
            labels=["reverse"]),
    ]
    batch = EventBatch(device_id="d1", tenant_id="t1", events=events)
    client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["vehicle_crossings"] == 2


def test_summary_staffing_coverage():
    client = _client()
    events = [
        _ev(camera_id="kitchen", type=EventType.occupancy_sample, mode=Mode.staffing,
            zone_id="grill", count=1),
        _ev(camera_id="kitchen", type=EventType.occupancy_sample, mode=Mode.staffing,
            zone_id="prep", count=0),
        _ev(camera_id="kitchen", type=EventType.occupancy_sample, mode=Mode.staffing,
            zone_id="dish", count=2),
    ]
    batch = EventBatch(device_id="d1", tenant_id="t1", events=events)
    client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["stations_total"] == 3
    assert s["totals"]["stations_unstaffed"] == 1   # only 'prep' is empty


def test_summary_avg_wait_none_without_queue():
    client = _client()
    batch = EventBatch(device_id="d1", tenant_id="t1", events=[
        _ev(type=EventType.entry, mode=Mode.retail, line_id="door")])
    client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    s = client.get("/v1/tenants/t1/summary").json()
    assert s["totals"]["avg_wait_seconds"] is None
    assert s["totals"]["wait_samples"] == 0


def test_summary_parking_free_after_leave():
    client = _client()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        _ev(camera_id="lot", type=EventType.vehicle_parked, mode=Mode.parking,
            zone_id="s1", ts=t0),
        _ev(camera_id="lot", type=EventType.vehicle_left, mode=Mode.parking,
            zone_id="s1", ts=t0 + timedelta(minutes=1)),
    ]
    batch = EventBatch(device_id="d1", tenant_id="t1", events=events)
    client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)

    s = client.get("/v1/tenants/t1/summary").json()
    # latest state for the space is "free", so nothing is counted occupied
    assert s["totals"]["parking_spaces_occupied"] == 0
    assert any(p["zone_id"] == "s1" and p["state"] == "free" for p in s["parking"])
