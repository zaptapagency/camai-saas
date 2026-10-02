"""Pure tests for the dataset builder + count-gate. No torch/ultralytics/cv2.

These synthesize a tiny capture root on disk (empty ``.jpg`` files are fine — the
builder only *copies* images, it never decodes them) and assert the YOLO dataset
is laid out, counted, and normalized correctly.
"""

from __future__ import annotations

import json
import os

from camai_training.dataset import (
    CLASS_NAMES,
    build_dataset,
    trusted_cameras_from_accuracy,
)


def _write_frame(cam_dir: str, ts_ms: int, frame_size, boxes) -> None:
    """Write one <ts_ms>.json sidecar + a matching (empty) <ts_ms>.jpg."""
    os.makedirs(cam_dir, exist_ok=True)
    stem = os.path.join(cam_dir, str(ts_ms))
    sidecar = {
        "camera_id": os.path.basename(cam_dir),
        "mode": "retail",
        "ts": "2026-01-01T00:00:00+00:00",
        "frame_size": list(frame_size),
        "predicted_count": len(boxes),
        "boxes": boxes,
    }
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh)
    # The builder only copies the jpg; an empty file is a valid stand-in.
    with open(stem + ".jpg", "wb") as fh:
        fh.write(b"")


def _make_capture_root(root: str) -> None:
    cam = os.path.join(root, "cam-1")
    # Frame A: one person box with KNOWN coords for the normalization assert,
    # plus an unknown class ("car") and a low-conf person that must both be dropped.
    _write_frame(cam, 1000, [100, 200], [
        {"cls_name": "person", "conf": 0.9, "xyxy": [40, 160, 60, 200]},
        {"cls_name": "car", "conf": 0.99, "xyxy": [0, 0, 10, 10]},          # unknown -> skipped
        {"cls_name": "person", "conf": 0.10, "xyxy": [1, 1, 2, 2]},          # low conf -> skipped
    ])
    # Frame B: one forklift box (a different known class).
    _write_frame(cam, 2000, [100, 200], [
        {"cls_name": "forklift", "conf": 0.8, "xyxy": [10, 10, 30, 50]},
    ])
    # Frame C: ONLY unskippable-by-rule boxes -> no usable box -> whole frame skipped.
    _write_frame(cam, 3000, [100, 200], [
        {"cls_name": "smoke", "conf": 0.2, "xyxy": [5, 5, 15, 15]},          # low conf
        {"cls_name": "bicycle", "conf": 0.9, "xyxy": [5, 5, 15, 15]},        # unknown
    ])


def test_build_dataset_summary_and_layout(tmp_path):
    root = str(tmp_path / "capture")
    out = str(tmp_path / "out")
    _make_capture_root(root)

    # val_frac=0 => everything lands in train, so counts are deterministic to assert.
    summary = build_dataset(root, out, val_frac=0.0)

    # Frame C had no usable box and must be skipped; A and B remain.
    assert summary["images"] == 2
    assert summary["train"] == 2
    assert summary["val"] == 0
    assert summary["cameras"] == ["cam-1"]
    assert summary["classes"] == CLASS_NAMES

    # data.yaml exists and declares the class space.
    assert os.path.isfile(summary["data_yaml"])
    with open(summary["data_yaml"], encoding="utf-8") as fh:
        yaml_text = fh.read()
    assert "nc: 6" in yaml_text
    assert "0: person" in yaml_text and "2: forklift" in yaml_text

    # Two label files + two copied images in the train split, none in val.
    lbl_train = os.path.join(out, "labels", "train")
    img_train = os.path.join(out, "images", "train")
    assert sorted(os.listdir(lbl_train)) == ["cam-1_1000.txt", "cam-1_2000.txt"]
    assert sorted(os.listdir(img_train)) == ["cam-1_1000.jpg", "cam-1_2000.jpg"]
    assert os.listdir(os.path.join(out, "labels", "val")) == []


def test_build_dataset_normalizes_and_skips(tmp_path):
    root = str(tmp_path / "capture")
    out = str(tmp_path / "out")
    _make_capture_root(root)
    build_dataset(root, out, val_frac=0.0)

    # Frame A -> exactly ONE line (the two other boxes were skipped), class id 0,
    # with coords normalized against frame_size [100, 200]:
    #   cx=(40+60)/2/100=0.5  cy=(160+200)/2/200=0.9  w=20/100=0.2  h=40/200=0.2
    with open(os.path.join(out, "labels", "train", "cam-1_1000.txt"), encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert len(lines) == 1
    cls, cx, cy, w, h = lines[0].split()
    assert cls == "0"
    assert abs(float(cx) - 0.5) < 1e-6
    assert abs(float(cy) - 0.9) < 1e-6
    assert abs(float(w) - 0.2) < 1e-6
    assert abs(float(h) - 0.2) < 1e-6

    # Frame B -> forklift is class id 2.
    with open(os.path.join(out, "labels", "train", "cam-1_2000.txt"), encoding="utf-8") as fh:
        assert fh.read().split()[0] == "2"


def test_include_cameras_filter(tmp_path):
    root = str(tmp_path / "capture")
    out = str(tmp_path / "out")
    _make_capture_root(root)
    # Add a second camera, then exclude it via include_cameras.
    _write_frame(os.path.join(root, "cam-2"), 1000, [100, 200],
                 [{"cls_name": "person", "conf": 0.9, "xyxy": [40, 160, 60, 200]}])

    summary = build_dataset(root, out, include_cameras=["cam-1"], val_frac=0.0)
    assert summary["cameras"] == ["cam-1"]


def test_deterministic_val_split(tmp_path):
    root = str(tmp_path / "capture")
    _make_capture_root(root)
    out_a = str(tmp_path / "a")
    out_b = str(tmp_path / "b")
    s1 = build_dataset(root, out_a, val_frac=0.5)
    s2 = build_dataset(root, out_b, val_frac=0.5)
    # Same inputs + same frac => identical split across runs (hash-based, not random).
    assert (s1["train"], s1["val"]) == (s2["train"], s2["val"])
    assert s1["train"] + s1["val"] == 2


def test_empty_capture_root(tmp_path):
    out = str(tmp_path / "out")
    summary = build_dataset(str(tmp_path / "does-not-exist"), out, val_frac=0.2)
    assert summary["images"] == 0
    assert summary["cameras"] == []
    assert os.path.isfile(summary["data_yaml"])  # still writes a (valid, empty) data.yaml


def test_trusted_cameras_from_accuracy():
    # No payload / no per_mode breakdown => can't map => include ALL (None).
    assert trusted_cameras_from_accuracy({}) is None
    assert trusted_cameras_from_accuracy({"overall": {"n": 5}}) is None

    # All modes within tolerance => nothing to gate out => include ALL (None).
    good = {"per_mode": [
        {"mode": "retail", "n": 10, "mae": 1.0, "mean_pct_error": 5.0},
        {"mode": "parking", "n": 8, "mae": 0.5, "mean_pct_error": 12.0},
    ]}
    assert trusted_cameras_from_accuracy(good, max_pct_error=20.0) is None

    # EVERY mode over the bar => count-gate fires => trust NOTHING (empty set).
    bad = {"per_mode": [
        {"mode": "retail", "n": 10, "mae": 9.0, "mean_pct_error": 40.0},
        {"mode": "parking", "n": 8, "mae": 7.0, "mean_pct_error": 33.0},
    ]}
    assert trusted_cameras_from_accuracy(bad, max_pct_error=20.0) == set()

    # Mixed (some good, some bad) => can't attribute mode->camera => include ALL.
    mixed = {"per_mode": [
        {"mode": "retail", "n": 10, "mean_pct_error": 5.0},
        {"mode": "parking", "n": 8, "mean_pct_error": 40.0},
    ]}
    assert trusted_cameras_from_accuracy(mixed, max_pct_error=20.0) is None
