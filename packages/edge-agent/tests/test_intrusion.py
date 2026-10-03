"""Regression tests for the intrusion counter.

Fixture-based: crafts person Detections directly, so it needs no torch/opencv and
no model. A central restricted zone spans the middle of a 100x100 frame.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.intrusion import IntrusionCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Box spans x±5 around foot_x, y from foot_y-30 to foot_y (head to feet).
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y)


def _camera() -> CameraConfig:
    return CameraConfig(
        id="back-lot", source="x", mode=Mode.intrusion,
        zones=[Zone(id="restricted", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def _alerts(events):
    return [e for e in events if e.type == EventType.intrusion_alert]


def test_entry_fires_one_alert():
    c = IntrusionCounter("t", "s", _camera())
    v = _alerts(c.update([_person(1)], FRAME, ts=0.0))
    assert len(v) == 1
    assert v[0].track_id == 1
    assert v[0].zone_id == "restricted"
    assert v[0].count == 1


def test_staying_is_debounced():
    c = IntrusionCounter("t", "s", _camera())
    first = _alerts(c.update([_person(1)], FRAME, ts=0.0))
    second = _alerts(c.update([_person(1)], FRAME, ts=0.5))
    third = _alerts(c.update([_person(1)], FRAME, ts=1.0))
    assert len(first) == 1
    assert second == []
    assert third == []


def test_person_outside_zone_no_alert():
    c = IntrusionCounter("t", "s", _camera())
    v = _alerts(c.update([_person(1, foot_x=10, foot_y=10)], FRAME, ts=0.0))
    assert v == []


def test_leaves_past_grace_then_reenters_refires():
    c = IntrusionCounter("t", "s", _camera())
    assert len(_alerts(c.update([_person(1)], FRAME, ts=0.0))) == 1   # enter -> fire
    # Leave the zone.
    assert _alerts(c.update([_person(1, foot_x=10, foot_y=10)], FRAME, ts=1.0)) == []
    # Still out, past the grace window.
    assert _alerts(c.update([_person(1, foot_x=10, foot_y=10)], FRAME, ts=7.0)) == []
    # Re-enter after grace -> re-fires.
    v = _alerts(c.update([_person(1)], FRAME, ts=8.0))
    assert len(v) == 1 and v[0].track_id == 1


def test_occupancy_sample_emitted_with_count():
    c = IntrusionCounter("t", "s", _camera())
    people = [_person(1, 40, 50), _person(2, 60, 50)]
    c.update(people, FRAME, ts=0.0)
    events = c.update(people, FRAME, ts=16.0)   # crosses the 15s sample window
    samples = [e for e in events if e.type == EventType.occupancy_sample]
    assert len(samples) == 1
    assert samples[0].count == 2
    assert samples[0].zone_id == "restricted"
