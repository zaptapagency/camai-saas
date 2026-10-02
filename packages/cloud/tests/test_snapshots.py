"""Accuracy-flywheel tests — snapshot ingest, labeling, and measured accuracy.

Exercises the five endpoints the edge + dashboard agents depend on end to end
via TestClient, with the standalone snapshot store pointed at a fresh temp
db/dir so runs are isolated and deterministic.
"""

import base64
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app import snapshots

# A tiny but structurally valid 1x1 baseline JPEG. Hardcoded so the test has no
# dependency on Pillow being installed.
_JPEG_1X1_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAC//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)


def _jpeg_bytes() -> bytes:
    try:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1, 1), (123, 222, 64)).save(buf, format="JPEG")
        return buf.getvalue()
    except Exception:
        return base64.b64decode(_JPEG_1X1_B64)


@pytest.fixture()
def client():
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    import importlib

    import app.main as main
    importlib.reload(main)
    # Point the standalone snapshot store at a fresh, isolated db + image dir.
    tmp = tempfile.mkdtemp()
    snapshots.configure(os.path.join(tmp, "snaps.db"), os.path.join(tmp, "imgs"))
    return TestClient(main.app)


def _post_snapshot(client, tenant="t1", camera="c1", mode="retail", predicted=5):
    return client.post(
        "/v1/ingest/snapshot",
        json={
            "tenant_id": tenant,
            "camera_id": camera,
            "mode": mode,
            "ts": "2026-10-02T12:00:00+00:00",
            "predicted_count": predicted,
            "image_b64": base64.b64encode(_jpeg_bytes()).decode("ascii"),
        },
    )


def test_ingest_snapshot_ok(client):
    r = _post_snapshot(client)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_snapshot_jpg_served(client):
    _post_snapshot(client)
    r = client.get("/v1/tenants/t1/cameras/c1/snapshot.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers.get("cache-control") == "no-store"
    assert r.content == _jpeg_bytes()


def test_snapshot_jpg_404_when_missing(client):
    r = client.get("/v1/tenants/t1/cameras/nope/snapshot.jpg")
    assert r.status_code == 404


def test_snapshot_meta(client):
    _post_snapshot(client, predicted=7)
    r = client.get("/v1/tenants/t1/cameras/c1/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["predicted_count"] == 7
    assert body["mode"] == "retail"
    assert body["labeled_count"] is None


def test_snapshot_meta_404_when_missing(client):
    assert client.get("/v1/tenants/t1/cameras/nope/snapshot").status_code == 404


def test_label_records_sample_and_marks_snapshot(client):
    _post_snapshot(client, predicted=5)
    r = client.post("/v1/tenants/t1/cameras/c1/label", json={"actual_count": 6})
    assert r.status_code == 200
    sample = r.json()
    assert sample["predicted"] == 5
    assert sample["actual"] == 6
    assert sample["mode"] == "retail"

    # labeled_count is now stamped on the latest snapshot.
    meta = client.get("/v1/tenants/t1/cameras/c1/snapshot").json()
    assert meta["labeled_count"] == 6


def test_label_404_when_no_snapshot(client):
    r = client.post("/v1/tenants/t1/cameras/nope/label", json={"actual_count": 3})
    assert r.status_code == 404


def test_accuracy(client):
    _post_snapshot(client, camera="c1", mode="retail", predicted=5)
    client.post("/v1/tenants/t1/cameras/c1/label", json={"actual_count": 6})
    _post_snapshot(client, camera="c2", mode="parking", predicted=10)
    client.post("/v1/tenants/t1/cameras/c2/label", json={"actual_count": 8})

    r = client.get("/v1/tenants/t1/accuracy")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "t1"
    assert body["overall"]["n"] == 2
    assert "mae" in body["overall"]
    assert "mean_pct_error" in body["overall"]
    modes = {m["mode"]: m for m in body["per_mode"]}
    assert modes["retail"]["n"] == 1
    assert modes["parking"]["n"] == 1
