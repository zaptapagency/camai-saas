"""Ingest API tests — the edge<->cloud contract.

Covers the two properties the pilot's billing and counts depend on: batches are
idempotent (a retried batch does not double-count) and cross-tenant events are
rejected.
"""

import os
import tempfile

from fastapi.testclient import TestClient

from camai_schema import Event, EventBatch, EventType, Mode


def _client():
    # Fresh DB per test run so counts are deterministic.
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    import importlib
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


_JSON = {"content-type": "application/json"}


def _batch(tenant="t1") -> EventBatch:
    events = [
        Event(tenant_id=tenant, site_id="s1", camera_id="c1",
              type=EventType.entry, mode=Mode.retail, line_id="door"),
        Event(tenant_id=tenant, site_id="s1", camera_id="c1",
              type=EventType.occupancy_sample, mode=Mode.retail, count=3),
    ]
    return EventBatch(device_id="d1", tenant_id=tenant, events=events)


def test_health():
    client = _client()
    assert client.get("/health").json()["status"] == "ok"


def test_ingest_is_idempotent():
    client = _client()
    batch = _batch()
    r1 = client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    assert r1.status_code == 200
    assert r1.json()["accepted"] == 2
    assert r1.json()["duplicates"] == 0

    # Re-send the exact same batch: everything is a duplicate, nothing double-counts.
    r2 = client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    assert r2.json()["accepted"] == 0
    assert r2.json()["duplicates"] == 2

    events = client.get("/v1/tenants/t1/events").json()
    assert len(events) == 2


def test_cross_tenant_events_rejected():
    client = _client()
    # A batch claiming tenant t1 but carrying a t2 event: the foreign event is dropped.
    ev_ok = Event(tenant_id="t1", site_id="s1", camera_id="c1",
                  type=EventType.entry, mode=Mode.retail, line_id="door")
    ev_bad = Event(tenant_id="t2", site_id="s1", camera_id="c1",
                   type=EventType.entry, mode=Mode.retail, line_id="door")
    batch = EventBatch(device_id="d1", tenant_id="t1", events=[ev_ok, ev_bad])
    r = client.post("/v1/ingest/events", content=batch.model_dump_json(), headers=_JSON)
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
