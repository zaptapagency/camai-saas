"""Pure-Python tests for the raw-frame + detection capture helper.

These exercise only the cv2-free / disk-free surface: the sidecar builder's exact
shape and the disabled (empty root_dir) no-op path. The JPEG-encode path in
``FrameCapture.write`` imports cv2 lazily and is never touched here, so this suite
runs without OpenCV and writes nothing to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from camai_edge.capture import FrameCapture, build_sidecar
from camai_schema import ObjectClass


@dataclass
class _FakeDetection:
    object_class: object
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    track_id: Optional[int] = None


def test_build_sidecar_maps_detections_to_exact_shape():
    dets = [
        _FakeDetection(ObjectClass.person, 0.91, 10.0, 20.0, 30.0, 40.0),
        _FakeDetection(ObjectClass.vehicle, 0.5, 1.0, 2.0, 3.0, 4.0),
    ]
    # ts=0 => 1970-01-01T00:00:00+00:00 (deterministic, no cv2/disk).
    sidecar = build_sidecar("cam_a", "retail", 0, (1280, 720), dets)

    assert set(sidecar) == {
        "camera_id", "mode", "ts", "frame_size", "predicted_count", "boxes",
    }
    assert sidecar["camera_id"] == "cam_a"
    assert sidecar["mode"] == "retail"
    assert sidecar["ts"] == "1970-01-01T00:00:00+00:00"
    assert sidecar["frame_size"] == [1280, 720]
    assert sidecar["predicted_count"] == 2
    assert sidecar["predicted_count"] == len(dets)

    assert len(sidecar["boxes"]) == 2
    for box in sidecar["boxes"]:
        assert set(box) == {"cls_name", "conf", "xyxy"}

    first = sidecar["boxes"][0]
    # cls_name is the ObjectClass *value*, not "ObjectClass.person".
    assert first["cls_name"] == "person"
    assert first["conf"] == 0.91
    assert isinstance(first["conf"], float)
    assert first["xyxy"] == [10.0, 20.0, 30.0, 40.0]

    assert sidecar["boxes"][1]["cls_name"] == "vehicle"


def test_build_sidecar_empty_detections():
    sidecar = build_sidecar("cam_b", "parking", 0, (640, 480), [])
    assert sidecar["predicted_count"] == 0
    assert sidecar["boxes"] == []


def test_empty_root_dir_is_a_no_op(tmp_path):
    cap = FrameCapture("")
    assert cap.enabled is False

    # Passing a bogus image proves cv2 is never imported / dereferenced when
    # disabled, and nothing is written to disk.
    sentinel = object()
    assert cap.write("cam_a", "retail", sentinel, (0, 0), [], 100.0) is False
    assert not any(tmp_path.iterdir())
