"""Regression tests for the crowd-density counter (crush-risk headcount vertical).

Fixture-based: crafts person Detections directly (as the detector would emit), so
it needs no torch/opencv and no model. FRAME is 100x100 and the zone covers the
centre with capacity=3 (the max safe headcount), so foot points in [30,70] land
inside it.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.crowd_density import CrowdDensityCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Box spans x±5 around foot_x, y from foot_y-30 to foot_y (head to feet).
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y)


def _inside(n: int) -> list[Detection]:
    """n people with foot points inside the centre zone."""
    return [_person(i, foot_x=40 + i, foot_y=50) for i in range(1, n + 1)]


def _camera(capacity=3) -> CameraConfig:
    return CameraConfig(
        id="gate", source="x", mode=Mode.crowd_density,
        zones=[Zone(id="pen", capacity=capacity, polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def _alerts(events):
    return [e for e in events if e.type == EventType.crowd_alert]


def _samples(events):
    return [e for e in events if e.type == EventType.occupancy_sample]


def test_within_threshold_no_alert():
    c = CrowdDensityCounter("t", "s", _camera())
    events = c.update(_inside(3), FRAME, ts=0.0)   # head == threshold, not over
    assert _alerts(events) == []


def test_person_outside_zone_not_counted():
    c = CrowdDensityCounter("t", "s", _camera())
    outside = [_person(i, foot_x=10, foot_y=10) for i in range(1, 6)]
    c.update(outside, FRAME, ts=0.0)
    events = c.update(outside, FRAME, ts=10.0)
    assert _alerts(events) == []


def test_over_threshold_grace_then_single_alert():
    c = CrowdDensityCounter("t", "s", _camera())
    over = _inside(4)  # 4 > threshold 3
    first = c.update(over, FRAME, ts=0.0)          # over starts, within grace
    assert _alerts(first) == []
    later = c.update(over, FRAME, ts=4.0)          # grace (3s) elapsed → commit
    a = _alerts(later)
    assert len(a) == 1
    assert a[0].count == 4
    assert a[0].zone_id == "pen"
    assert a[0].labels == ["threshold:3"]


def test_alert_debounced_while_staying_over():
    c = CrowdDensityCounter("t", "s", _camera())
    over = _inside(4)
    c.update(over, FRAME, ts=0.0)
    c.update(over, FRAME, ts=4.0)                  # commits, alert fires here
    again = c.update(over, FRAME, ts=5.0)          # still over → no new alert
    assert _alerts(again) == []


def test_alert_rearms_and_refires():
    c = CrowdDensityCounter("t", "s", _camera())
    over = _inside(4)
    c.update(over, FRAME, ts=0.0)
    assert len(_alerts(c.update(over, FRAME, ts=4.0))) == 1   # first alert
    # Drop back within the threshold → re-arm.
    rearm = c.update(_inside(1), FRAME, ts=5.0)
    assert _alerts(rearm) == []
    # Go over again and let the grace elapse → a fresh alert fires.
    c.update(over, FRAME, ts=6.0)
    refire = c.update(over, FRAME, ts=10.0)
    a = _alerts(refire)
    assert len(a) == 1 and a[0].count == 4 and a[0].labels == ["threshold:3"]


def test_no_threshold_never_alerts():
    c = CrowdDensityCounter("t", "s", _camera(capacity=None))
    crowd = _inside(5)
    c.update(crowd, FRAME, ts=0.0)
    events = c.update(crowd, FRAME, ts=30.0)       # well past any grace
    assert _alerts(events) == []


def test_periodic_occupancy_sample():
    c = CrowdDensityCounter("t", "s", _camera())
    people = _inside(2)                             # within threshold
    c.update(people, FRAME, ts=0.0)                # no periodic at t0
    events = c.update(people, FRAME, ts=16.0)      # crosses the 15s window
    s = _samples(events)
    assert len(s) == 1
    assert s[0].count == 2
    assert s[0].zone_id == "pen"
    assert s[0].labels is None                      # not over-threshold


def test_state_change_sample_tagged_over():
    c = CrowdDensityCounter("t", "s", _camera())
    over = _inside(4)
    c.update(over, FRAME, ts=0.0)
    events = c.update(over, FRAME, ts=4.0)          # commit flip → immediate sample
    s = _samples(events)
    assert len(s) == 1
    assert s[0].labels == ["over"] and s[0].count == 4
