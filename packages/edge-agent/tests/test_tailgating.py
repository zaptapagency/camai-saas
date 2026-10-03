"""Regression tests for the tailgating counter (people piggybacking a secure line)."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Line, Point
from camai_edge.counting.tailgating import TailgatingCounter
from camai_edge.geometry import line_side
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_y: float) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=48, y1=foot_y - 15, x2=52, y2=foot_y)


def _camera() -> CameraConfig:
    # One horizontal line across the middle of the frame.
    return CameraConfig(
        id="door", source="x", mode=Mode.tailgating,
        lines=[Line(id="secure", a=Point(x=0.1, y=0.5), b=Point(x=0.9, y=0.5))],
    )


def _alerts(events):
    return [e for e in events if e.type == EventType.tailgating_alert]


def test_crossing_detected_via_line_side_sign_change():
    # Foot point below the line (y=60) vs above (y=40) are opposite signs.
    below = line_side(50, 60, 10, 50, 90, 50)
    above = line_side(50, 40, 10, 50, 90, 50)
    assert (below >= 0) != (above >= 0)


def test_single_person_no_alert():
    c = TailgatingCounter("t", "s", _camera())
    assert c.update([_person(1, 60)], FRAME, ts=0.0) == []    # establish side
    events = c.update([_person(1, 40)], FRAME, ts=1.0)        # one crossing
    assert _alerts(events) == []


def test_two_people_within_window_one_alert():
    c = TailgatingCounter("t", "s", _camera())
    # Both start below the line.
    assert c.update([_person(1, 60), _person(2, 60)], FRAME, ts=0.0) == []
    # Both cross to the other side within the 2s window.
    events = c.update([_person(1, 40), _person(2, 40)], FRAME, ts=0.5)
    alerts = _alerts(events)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.line_id == "secure"
    assert a.count >= 2
    assert a.labels == ["2_together"]
    assert a.object_class == "person"


def test_two_crossings_far_apart_no_alert():
    c = TailgatingCounter("t", "s", _camera())
    c.update([_person(1, 60), _person(2, 60)], FRAME, ts=0.0)
    # Person 1 crosses at t=1.0.
    assert _alerts(c.update([_person(1, 40), _person(2, 60)], FRAME, ts=1.0)) == []
    # Person 2 crosses much later (> 2s window) -> not clustered.
    events = c.update([_person(1, 40), _person(2, 40)], FRAME, ts=5.0)
    assert _alerts(events) == []


def test_cluster_fires_once_then_resets():
    c = TailgatingCounter("t", "s", _camera())
    c.update([_person(1, 60), _person(2, 60)], FRAME, ts=0.0)
    first = _alerts(c.update([_person(1, 40), _person(2, 40)], FRAME, ts=0.5))
    assert len(first) == 1
    # Same frame repeated: no new crossing, so no second alert from the same cluster.
    again = _alerts(c.update([_person(1, 40), _person(2, 40)], FRAME, ts=0.8))
    assert again == []
