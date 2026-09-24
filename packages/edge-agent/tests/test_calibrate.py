"""Tests for calibration geometry write-back.

Covers the pure config-editing path (no OpenCV / web server needed): geometry is
validated through the same models the agent uses, persisted, and reloadable.
"""

import textwrap

import pytest

from camai_edge.calibrate import apply_geometry
from camai_edge.config import SiteConfig

_CONFIG = textwrap.dedent("""
    tenant_id: t1
    site_id: s1
    device_id: d1
    cameras:
      - id: cam-door
        source: sample.mp4
        mode: retail
      - id: cam-lot
        source: sample.mp4
        mode: parking
""")


def _write(tmp_path):
    p = tmp_path / "site.yaml"
    p.write_text(_CONFIG, encoding="utf-8")
    return p


def test_apply_line_geometry_persists(tmp_path):
    p = _write(tmp_path)
    cam = apply_geometry(
        p, "cam-door",
        lines=[{"id": "door", "a": {"x": 0.1, "y": 0.5},
                "b": {"x": 0.9, "y": 0.5}, "invert": True}],
        zones=[],
    )
    assert len(cam.lines) == 1
    assert cam.lines[0].id == "door"
    assert cam.lines[0].invert is True

    # Reloading the file from disk shows the persisted geometry.
    reloaded = SiteConfig.load(p)
    door = next(c for c in reloaded.cameras if c.id == "cam-door")
    assert len(door.lines) == 1
    # The other camera is untouched.
    lot = next(c for c in reloaded.cameras if c.id == "cam-lot")
    assert lot.zones == []


def test_apply_zone_geometry_persists(tmp_path):
    p = _write(tmp_path)
    cam = apply_geometry(
        p, "cam-lot", lines=[],
        zones=[{"id": "space-1", "capacity": 1, "polygon": [
            {"x": 0.3, "y": 0.3}, {"x": 0.7, "y": 0.3}, {"x": 0.5, "y": 0.7}]}],
    )
    assert len(cam.zones) == 1
    assert cam.zones[0].capacity == 1
    assert len(cam.zones[0].polygon) == 3


def test_out_of_range_coordinates_rejected(tmp_path):
    p = _write(tmp_path)
    with pytest.raises(Exception):  # pydantic ValidationError
        apply_geometry(
            p, "cam-door",
            lines=[{"id": "bad", "a": {"x": 1.5, "y": 0.5}, "b": {"x": 0.9, "y": 0.5}}],
            zones=[],
        )


def test_unknown_camera_raises(tmp_path):
    p = _write(tmp_path)
    with pytest.raises(KeyError):
        apply_geometry(p, "nope", lines=[], zones=[])
