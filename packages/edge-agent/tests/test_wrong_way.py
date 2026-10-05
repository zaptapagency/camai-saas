"""Regression tests for the wrong-way driving counter."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Line, Point
from camai_edge.counting.wrong_way import WrongWayCounter
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
        id="ramp", source="x", mode=Mode.wrong_way,
        lines=[Line(id="lane", a=Point(x=0.1, y=0.5), b=Point(x=0.9, y=0.5), invert=invert)])


def _alerts(events):
    return [e for e in events if e.type == EventType.wrong_way_alert]


def test_forward_crossing_no_alert():
    c = WrongWayCounter("t", "s", _camera())
    c.update([_veh(1, 60)], FRAME, ts=0.0)            # establish side
    out = c.update([_veh(1, 40)], FRAME, ts=1.0)      # allowed direction
    assert _alerts(out) == []


def test_reverse_crossing_emits_alert():
    c = WrongWayCounter("t", "s", _camera())
    c.update([_veh(1, 40)], FRAME, ts=0.0)
    out = c.update([_veh(1, 60)], FRAME, ts=1.0)      # against allowed direction
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].line_id == "lane"
    assert alerts[0].labels == ["wrong_way"]
    assert alerts[0].object_class == "vehicle"


def test_invert_flips_allowed_direction():
    c = WrongWayCounter("t", "s", _camera(invert=True))
    c.update([_veh(1, 60)], FRAME, ts=0.0)
    out = c.update([_veh(1, 40)], FRAME, ts=1.0)      # forward geometry, flipped ⇒ wrong-way
    assert len(_alerts(out)) == 1


def test_people_not_counted():
    c = WrongWayCounter("t", "s", _camera())
    c.update([_person(1, 40)], FRAME, ts=0.0)
    out = c.update([_person(1, 60)], FRAME, ts=1.0)
    assert _alerts(out) == []


def test_vehicle_type_preserved():
    c = WrongWayCounter("t", "s", _camera())
    c.update([_veh(1, 40, cls=ObjectClass.truck)], FRAME, ts=0.0)
    out = c.update([_veh(1, 60, cls=ObjectClass.truck)], FRAME, ts=1.0)
    assert _alerts(out)[0].object_class == "truck"
