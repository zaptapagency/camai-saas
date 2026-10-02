"""Pure-Python tests for the annotated-snapshot uploader.

These deliberately exercise only the cv2-free / network-free surface: the payload
builder, the disabled (empty ingest_url) no-op path, and the per-camera interval
gating. The JPEG-encode path (``_encode``) imports cv2 lazily and is never touched
here, so this suite runs without OpenCV or a network.
"""

from __future__ import annotations

from camai_edge.snapshots import SnapshotUploader, _payload


def test_payload_has_exact_cloud_contract_keys():
    body = _payload(
        tenant_id="tnt_1",
        camera_id="cam_a",
        mode="retail",
        predicted_count=3,
        ts="2026-10-02T12:00:00+00:00",
        image_b64="Zm9v",
    )
    assert set(body) == {
        "tenant_id", "camera_id", "mode", "ts", "predicted_count", "image_b64",
    }
    assert body["tenant_id"] == "tnt_1"
    assert body["camera_id"] == "cam_a"
    assert body["mode"] == "retail"
    assert body["predicted_count"] == 3
    assert body["ts"] == "2026-10-02T12:00:00+00:00"
    assert body["image_b64"] == "Zm9v"


def test_empty_ingest_url_is_a_no_op():
    up = SnapshotUploader("", "tnt_1", "site_1")
    assert up.enabled is False

    # Neither path touches cv2 or the network, and nothing raises. Passing a bogus
    # image / counter proves they're never dereferenced when disabled.
    sentinel = object()
    assert up.upload("cam_a", "retail", sentinel, (0, 0), 1, sentinel, 100.0) is False
    assert up.maybe_upload(
        "cam_a", "retail", sentinel, (0, 0), 1, sentinel, 100.0, 5.0
    ) is False


def test_maybe_upload_gates_on_interval_per_camera(monkeypatch):
    up = SnapshotUploader("https://ingest.example", "tnt_1", "site_1")
    assert up.enabled is True

    calls: list[tuple[str, float]] = []

    def _fake_upload(camera_id, mode, image, frame_size, predicted_count, counter, ts):
        calls.append((camera_id, ts))
        return True

    # Replace the cv2/network send so gating is tested in isolation.
    monkeypatch.setattr(up, "upload", _fake_upload)

    # First send goes through and arms the per-camera clock.
    assert up.maybe_upload("cam_a", "retail", None, (0, 0), 0, None, 100.0, 10.0) is True
    # Within the interval: suppressed.
    assert up.maybe_upload("cam_a", "retail", None, (0, 0), 0, None, 105.0, 10.0) is False
    # Interval elapsed: sends again.
    assert up.maybe_upload("cam_a", "retail", None, (0, 0), 0, None, 111.0, 10.0) is True

    # A different camera has its own independent clock.
    assert up.maybe_upload("cam_b", "parking", None, (0, 0), 0, None, 106.0, 10.0) is True

    assert calls == [("cam_a", 100.0), ("cam_a", 111.0), ("cam_b", 106.0)]


def test_maybe_upload_disabled_when_interval_not_positive(monkeypatch):
    up = SnapshotUploader("https://ingest.example", "tnt_1", "site_1")
    monkeypatch.setattr(up, "upload", lambda *a, **k: pytest_fail())

    assert up.maybe_upload("cam_a", "retail", None, (0, 0), 0, None, 100.0, 0.0) is False
    # Nothing was recorded, so the next positive-interval call is the first send.
    assert "cam_a" not in up._last_sent


def pytest_fail():  # pragma: no cover - only reached on a gating bug
    raise AssertionError("upload() must not be called when interval <= 0")
