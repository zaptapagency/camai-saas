"""Regression tests for the smart-city traffic counter (directional vehicle flow)."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Line, Point
from camai_edge.counting.traffic import TrafficCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _veh(track_id: int, foot_y: float, cls: ObjectClass = ObjectClass.vehicle) -> Detection:
    return Detection(track_id=track_id, object_class=cls, confidence=0.9,
                     x1=45, y1=foot_y - 15, x2=55, y2=foot_y)


def _person(track_id: int, foot_y: float) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=48, y1=foot_y - 15, x2=52, y2=foot_y)


def _camera(invert=False) -> CameraConfig:
    return CameraConfig(
        id="road", source="x", mode=Mode.traffic,
        lines=[Line(id="cordon", a=Point(x=0.1, y=0.5), b=Point(x=0.9, y=0.5), invert=invert)],
    )


def test_forward_crossing():
    c = TrafficCounter("t", "s", _camera())
    assert c.update([_veh(1, 60)], FRAME, ts=0.0) == []       # establish side
    events = c.update([_veh(1, 40)], FRAME, ts=1.0)           # cross to other side
    x = [e for e in events if e.type == EventType.vehicle_crossing]
    assert len(x) == 1
    assert x[0].line_id == "cordon"
    assert x[0].labels == ["forward"]
    assert x[0].object_class == "vehicle"


def test_reverse_crossing():
    c = TrafficCounter("t", "s", _camera())
    c.update([_veh(1, 40)], FRAME, ts=0.0)
    events = c.update([_veh(1, 60)], FRAME, ts=1.0)
    x = [e for e in events if e.type == EventType.vehicle_crossing]
    assert len(x) == 1 and x[0].labels == ["reverse"]


def test_invert_flips_direction():
    c = TrafficCounter("t", "s", _camera(invert=True))
    c.update([_veh(1, 60)], FRAME, ts=0.0)
    events = c.update([_veh(1, 40)], FRAME, ts=1.0)           # forward geometry...
    x = [e for e in events if e.type == EventType.vehicle_crossing]
    assert x[0].labels == ["reverse"]                        # ...flipped by invert


def test_class_breakdown_preserved():
    c = TrafficCounter("t", "s", _camera())
    c.update([_veh(1, 60, cls=ObjectClass.truck)], FRAME, ts=0.0)
    events = c.update([_veh(1, 40, cls=ObjectClass.truck)], FRAME, ts=1.0)
    x = [e for e in events if e.type == EventType.vehicle_crossing]
    assert x[0].object_class == "truck"


def test_people_not_counted():
    c = TrafficCounter("t", "s", _camera())
    c.update([_person(1, 60)], FRAME, ts=0.0)
    events = c.update([_person(1, 40)], FRAME, ts=1.0)
    assert [e for e in events if e.type == EventType.vehicle_crossing] == []


def test_no_crossing_same_side():
    c = TrafficCounter("t", "s", _camera())
    c.update([_veh(1, 60)], FRAME, ts=0.0)
    events = c.update([_veh(1, 70)], FRAME, ts=1.0)           # moved, same side
    assert [e for e in events if e.type == EventType.vehicle_crossing] == []
