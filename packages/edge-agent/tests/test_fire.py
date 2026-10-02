"""Regression tests for the Tier-3 fire/smoke hazard counter.

Fixture-based: crafts fire/smoke Detections directly (as a hazard-trained detector
would emit), so it needs no torch/opencv and no fire model.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.fire import FireCounter, _CLEAR_GRACE_SECONDS
from camai_edge.detect import Detection

FRAME = (100, 100)


def _hazard(obj: ObjectClass, cx: float = 50.0, cy: float = 50.0) -> Detection:
    return Detection(track_id=None, object_class=obj, confidence=0.9,
                     x1=cx - 3, y1=cy - 3, x2=cx + 3, y2=cy + 3)


def _camera_no_zones() -> CameraConfig:
    return CameraConfig(id="cam", source="x", mode=Mode.fire, zones=[])


def _camera_zoned() -> CameraConfig:
    # Central zone spanning x,y in [0.3, 0.7].
    return CameraConfig(
        id="cam", source="x", mode=Mode.fire,
        zones=[Zone(id="floor", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def _alerts(events):
    return [e for e in events if e.type == EventType.hazard_alert]


def test_no_hazards_no_alert():
    c = FireCounter("t", "s", _camera_no_zones())
    events = c.update([], FRAME, ts=0.0)
    assert _alerts(events) == []


def test_smoke_appears_fires_once():
    c = FireCounter("t", "s", _camera_no_zones())
    events = c.update([_hazard(ObjectClass.smoke)], FRAME, ts=0.0)
    a = _alerts(events)
    assert len(a) == 1
    assert a[0].labels == ["smoke"]
    assert a[0].count == 1
    assert a[0].zone_id is None


def test_same_smoke_debounced():
    c = FireCounter("t", "s", _camera_no_zones())
    first = c.update([_hazard(ObjectClass.smoke)], FRAME, ts=0.0)
    second = c.update([_hazard(ObjectClass.smoke)], FRAME, ts=0.5)
    assert len(_alerts(first)) == 1
    assert _alerts(second) == []


def test_fire_also_appears_refires_with_both_labels():
    c = FireCounter("t", "s", _camera_no_zones())
    c.update([_hazard(ObjectClass.smoke)], FRAME, ts=0.0)
    events = c.update(
        [_hazard(ObjectClass.smoke, 40, 40), _hazard(ObjectClass.fire, 60, 60)],
        FRAME, ts=1.0,
    )
    a = _alerts(events)
    assert len(a) == 1
    assert a[0].labels == ["fire", "smoke"]
    assert a[0].count == 2
    assert a[0].zone_id is None


def test_clear_grace_rearms_then_refires():
    c = FireCounter("t", "s", _camera_no_zones())
    assert len(_alerts(c.update([_hazard(ObjectClass.smoke)], FRAME, ts=0.0))) == 1
    # Hazard gone; keep feeding clear frames until past the grace window.
    c.update([], FRAME, ts=1.0)
    c.update([], FRAME, ts=1.0 + _CLEAR_GRACE_SECONDS + 0.1)
    # A fresh hazard should re-fire now that the scope re-armed.
    events = c.update([_hazard(ObjectClass.smoke)], FRAME, ts=10.0)
    a = _alerts(events)
    assert len(a) == 1 and a[0].labels == ["smoke"]


def test_single_frame_dropout_within_grace_does_not_rearm():
    c = FireCounter("t", "s", _camera_no_zones())
    assert len(_alerts(c.update([_hazard(ObjectClass.smoke)], FRAME, ts=0.0))) == 1
    # Brief dropout well inside the grace window...
    c.update([], FRAME, ts=1.0)
    # ...then the same smoke returns: still committed, so no duplicate alert.
    events = c.update([_hazard(ObjectClass.smoke)], FRAME, ts=2.0)
    assert _alerts(events) == []


def test_zone_scoped_hazard_outside_ignored_inside_alerts():
    c = FireCounter("t", "s", _camera_zoned())
    # Centroid at (10,10) is outside the central [30,70] zone -> ignored.
    outside = c.update([_hazard(ObjectClass.smoke, 10, 10)], FRAME, ts=0.0)
    assert _alerts(outside) == []
    # Centroid at (50,50) is inside -> alert carrying the zone id.
    inside = c.update([_hazard(ObjectClass.smoke, 50, 50)], FRAME, ts=1.0)
    a = _alerts(inside)
    assert len(a) == 1
    assert a[0].labels == ["smoke"]
    assert a[0].zone_id == "floor"
    assert a[0].count == 1
