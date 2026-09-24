"""Regression tests for the queue-length counter (queue length + wait time).

Fixture-based: crafts Detection objects directly, so it runs with no torch/opencv.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.queue import QueueCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 20, x2=foot_x + 5, y2=foot_y)


def _vehicle(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.vehicle, confidence=0.9,
                     x1=foot_x - 10, y1=foot_y - 20, x2=foot_x + 10, y2=foot_y)


def _camera(capacity=None) -> CameraConfig:
    return CameraConfig(
        id="till", source="x", mode=Mode.queue,
        zones=[Zone(id="q1", capacity=capacity, polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def test_wait_time_emitted_on_leave():
    c = QueueCounter("t", "s", _camera())
    assert c.update([_person(1)], FRAME, ts=0.0) == []      # enters
    assert c.update([_person(1)], FRAME, ts=5.0) == []      # still waiting
    events = c.update([], FRAME, ts=8.0)                    # gone > grace (2s) -> left
    dwell = [e for e in events if e.type == EventType.dwell]
    assert len(dwell) == 1
    assert dwell[0].zone_id == "q1"
    assert dwell[0].track_id == 1
    assert dwell[0].dwell_seconds == 5.0                   # last_seen(5) - enter(0)


def test_grace_period_absorbs_brief_occlusion():
    c = QueueCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)
    c.update([], FRAME, ts=1.0)                             # missed 1 frame (< grace)
    events = c.update([_person(1)], FRAME, ts=2.0)          # reappears
    # No dwell yet — the person never "left"; their wait is continuous.
    assert [e for e in events if e.type == EventType.dwell] == []


def test_queue_length_sample():
    c = QueueCounter("t", "s", _camera())
    three = [_person(1, 40, 50), _person(2, 50, 50), _person(3, 60, 50)]
    c.update(three, FRAME, ts=0.0)                          # before first sample window
    events = c.update(three, FRAME, ts=16.0)               # crosses 15s sample interval
    samples = [e for e in events if e.type == EventType.occupancy_sample]
    assert len(samples) == 1
    assert samples[0].zone_id == "q1"
    assert samples[0].count == 3


def test_only_people_counted():
    c = QueueCounter("t", "s", _camera())
    # A vehicle parked over the queue zone must not register as a waiter.
    c.update([_vehicle(9)], FRAME, ts=0.0)
    events = c.update([], FRAME, ts=8.0)
    assert [e for e in events if e.type == EventType.dwell] == []


def test_person_outside_zone_not_counted():
    c = QueueCounter("t", "s", _camera())
    c.update([_person(1, foot_x=10, foot_y=10)], FRAME, ts=0.0)   # top-left, outside
    events = c.update([], FRAME, ts=8.0)
    assert [e for e in events if e.type == EventType.dwell] == []
