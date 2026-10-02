"""Regression tests for the capacity counter (Tier-1 occupancy-limit vertical).

Fixture-based: crafts person Detections directly (as the detector would emit), so
it needs no torch/opencv and no model. FRAME is 100x100 and the zone covers the
centre with capacity=2, so foot points in [30,70] land inside it.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.capacity import CapacityCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Box spans x±5 around foot_x, y from foot_y-30 to foot_y (head to feet).
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y)


def _inside(n: int) -> list[Detection]:
    """n people with foot points inside the centre zone."""
    return [_person(i, foot_x=40 + i, foot_y=50) for i in range(1, n + 1)]


def _camera(capacity=2) -> CameraConfig:
    return CameraConfig(
        id="room", source="x", mode=Mode.capacity,
        zones=[Zone(id="hall", capacity=capacity, polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def _breaches(events):
    return [e for e in events if e.type == EventType.capacity_breach]


def _samples(events):
    return [e for e in events if e.type == EventType.occupancy_sample]


def test_within_limit_no_breach():
    c = CapacityCounter("t", "s", _camera())
    events = c.update(_inside(2), FRAME, ts=0.0)   # head == limit, not over
    assert _breaches(events) == []


def test_person_outside_zone_not_counted():
    c = CapacityCounter("t", "s", _camera())
    # Three people, but all outside the centre zone → head 0, no breach ever.
    outside = [_person(i, foot_x=10, foot_y=10) for i in range(1, 4)]
    c.update(outside, FRAME, ts=0.0)
    events = c.update(outside, FRAME, ts=10.0)
    assert _breaches(events) == []


def test_over_limit_grace_then_single_breach():
    c = CapacityCounter("t", "s", _camera())
    over = _inside(3)  # 3 > limit 2
    first = c.update(over, FRAME, ts=0.0)          # over starts, within grace
    assert _breaches(first) == []
    later = c.update(over, FRAME, ts=6.0)          # grace (5s) elapsed → commit
    b = _breaches(later)
    assert len(b) == 1
    assert b[0].count == 3
    assert b[0].zone_id == "hall"
    assert b[0].labels == ["limit:2"]


def test_breach_debounced_while_staying_over():
    c = CapacityCounter("t", "s", _camera())
    over = _inside(3)
    c.update(over, FRAME, ts=0.0)
    c.update(over, FRAME, ts=6.0)                  # commits, breach fires here
    again = c.update(over, FRAME, ts=7.0)         # still over → no new breach
    assert _breaches(again) == []


def test_breach_rearms_and_refires():
    c = CapacityCounter("t", "s", _camera())
    over = _inside(3)
    c.update(over, FRAME, ts=0.0)
    assert len(_breaches(c.update(over, FRAME, ts=6.0))) == 1   # first breach
    # Drop back within the limit → re-arm.
    rearm = c.update(_inside(1), FRAME, ts=7.0)
    assert _breaches(rearm) == []
    # Go over again and let the grace elapse → a fresh breach fires.
    c.update(over, FRAME, ts=8.0)
    refire = c.update(over, FRAME, ts=14.0)
    b = _breaches(refire)
    assert len(b) == 1 and b[0].count == 3 and b[0].labels == ["limit:2"]


def test_no_limit_never_breaches():
    c = CapacityCounter("t", "s", _camera(capacity=None))
    crowd = _inside(5)
    c.update(crowd, FRAME, ts=0.0)
    events = c.update(crowd, FRAME, ts=30.0)       # well past any grace
    assert _breaches(events) == []


def test_periodic_occupancy_sample():
    c = CapacityCounter("t", "s", _camera())
    people = _inside(1)                             # within limit
    c.update(people, FRAME, ts=0.0)                # no periodic at t0
    events = c.update(people, FRAME, ts=16.0)      # crosses the 15s window
    s = _samples(events)
    assert len(s) == 1
    assert s[0].count == 1
    assert s[0].zone_id == "hall"
    assert s[0].labels is None                      # not over-limit


def test_state_change_sample_tagged_over():
    c = CapacityCounter("t", "s", _camera())
    over = _inside(3)
    c.update(over, FRAME, ts=0.0)
    events = c.update(over, FRAME, ts=6.0)          # commit flip → immediate sample
    s = _samples(events)
    assert len(s) == 1
    assert s[0].labels == ["over"] and s[0].count == 3
