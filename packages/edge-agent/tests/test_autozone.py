"""Tests for the auto-calibration assist (autozone).

Fixture-based: crafts person Detections directly at chosen *normalized* foot
positions (converted to pixels for the given frame), so it needs no torch/opencv
and no model. Verifies suggested zones actually cover the activity they came from
by resolving them back to pixels and running the real point-in-polygon test.
"""

from camai_schema import Mode, ObjectClass

from camai_edge.autozone import (
    accumulate,
    suggest_geometry,
    suggest_line,
    suggest_zones,
)
from camai_edge.detect import Detection
from camai_edge.geometry import point_in_polygon, resolve_zone

FRAME = (640, 480)


def _person(nx: float, ny: float, frame_size=FRAME, track_id: int = 1) -> Detection:
    """A person whose *foot point* lands at normalized (nx, ny) in the frame.

    foot_point is ((x1+x2)/2, y2), so a box centered on nx*w with its bottom at
    ny*h puts the foot exactly there.
    """
    w, h = frame_size
    fx, fy = nx * w, ny * h
    return Detection(
        track_id=track_id,
        object_class=ObjectClass.person,
        confidence=0.9,
        x1=fx - 5,
        y1=fy - 30,
        x2=fx + 5,
        y2=fy,
    )


def _cluster(cx: float, cy: float, n: int = 40, spread: float = 0.03) -> list[Detection]:
    """n points in a tight deterministic lattice around normalized (cx, cy)."""
    pts: list[Detection] = []
    side = int(n ** 0.5) or 1
    for i in range(n):
        gx = (i % side) / max(1, side - 1) - 0.5  # -0.5..0.5
        gy = (i // side) / max(1, side - 1) - 0.5
        pts.append(_person(cx + gx * spread, cy + gy * spread, track_id=i + 1))
    return pts


def _coords_in_unit(obj_polys) -> bool:
    return all(0.0 <= p.x <= 1.0 and 0.0 <= p.y <= 1.0 for p in obj_polys)


def test_accumulate_flattens_frames():
    frame_a = _cluster(0.5, 0.5, n=9)
    frame_b = _cluster(0.5, 0.5, n=9)
    flat = accumulate([frame_a, frame_b])
    assert len(flat) == 18
    # A flat list passes through unchanged.
    assert len(accumulate(flat)) == 18


def test_center_cluster_zone_contains_centroid():
    dets = _cluster(0.5, 0.5, n=49, spread=0.04)
    zones = suggest_zones(dets, FRAME)
    assert len(zones) >= 1

    # Centroid of the cluster, in pixels.
    foots = [d.foot_point for d in dets]
    cxp = sum(f[0] for f in foots) / len(foots)
    cyp = sum(f[1] for f in foots) / len(foots)

    poly = resolve_zone(zones[0], *FRAME)
    assert point_in_polygon(cxp, cyp, poly)
    # All coords normalized.
    for z in zones:
        assert _coords_in_unit(z.polygon)


def test_two_separated_clusters_two_zones():
    dets = _cluster(0.25, 0.5, n=36) + _cluster(0.75, 0.5, n=36)
    zones = suggest_zones(dets, FRAME, max_zones=3)
    assert 1 <= len(zones) <= 2
    # With two balanced, well-separated clusters we expect two distinct zones.
    assert len(zones) == 2
    ids = {z.id for z in zones}
    assert ids == {"zone-1", "zone-2"}
    for z in zones:
        assert _coords_in_unit(z.polygon)


def test_max_zones_and_min_share_respected():
    dets = _cluster(0.2, 0.2, n=36) + _cluster(0.8, 0.2, n=36) + _cluster(0.5, 0.8, n=36)
    zones = suggest_zones(dets, FRAME, max_zones=2)
    assert len(zones) == 2  # capped
    # A tiny stray cluster below min_share must be dropped.
    big = _cluster(0.5, 0.5, n=100, spread=0.02)
    stray = [_person(0.9, 0.05, track_id=999)]
    zones2 = suggest_zones(big + stray, FRAME, min_share=0.08)
    assert len(zones2) == 1


def test_empty_detections_no_zones_no_line():
    assert suggest_zones([], FRAME) == []
    assert suggest_line([], FRAME) is None


def test_line_vertical_movement_is_horizontal():
    # Points streaming top -> bottom: large y-spread, small x-spread.
    dets = [_person(0.5, ny / 20.0, track_id=i) for i, ny in enumerate(range(1, 20))]
    line = suggest_line(dets, FRAME)
    assert line is not None
    assert line.id == "line-1"
    # Roughly horizontal: endpoints share the same y, span full width.
    assert abs(line.a.y - line.b.y) < 1e-6
    assert line.a.x == 0.0 and line.b.x == 1.0
    assert _coords_in_unit([line.a, line.b])


def test_line_horizontal_movement_is_vertical():
    # Points streaming left -> right: large x-spread, small y-spread.
    dets = [_person(nx / 20.0, 0.5, track_id=i) for i, nx in enumerate(range(1, 20))]
    line = suggest_line(dets, FRAME)
    assert line is not None
    # Roughly vertical: endpoints share the same x, span full height.
    assert abs(line.a.x - line.b.x) < 1e-6
    assert line.a.y == 0.0 and line.b.y == 1.0


def test_too_few_detections_no_line():
    assert suggest_line([_person(0.5, 0.1), _person(0.5, 0.9)], FRAME) is None


def test_suggest_geometry_retail_yields_line():
    dets = [_person(0.5, ny / 20.0, track_id=i) for i, ny in enumerate(range(1, 20))]
    geo = suggest_geometry(dets, FRAME, Mode.retail)
    assert geo["zones"] == []
    assert len(geo["lines"]) == 1
    assert geo["lines"][0].id == "line-1"


def test_suggest_geometry_parking_yields_zones():
    dets = _cluster(0.3, 0.5, n=49) + _cluster(0.7, 0.5, n=49)
    geo = suggest_geometry(dets, FRAME, Mode.parking)
    assert geo["lines"] == []
    assert len(geo["zones"]) >= 1
    for z in geo["zones"]:
        assert _coords_in_unit(z.polygon)


def test_suggest_geometry_accepts_string_mode():
    dets = [_person(nx / 20.0, 0.5, track_id=i) for i, nx in enumerate(range(1, 20))]
    geo = suggest_geometry(dets, FRAME, "traffic")
    assert len(geo["lines"]) == 1
