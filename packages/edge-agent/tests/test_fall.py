"""Regression tests for the fall-detection counter."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.fall import FallCounter, _FALL_CONFIRM_SECONDS
from camai_edge.detect import Detection

FRAME = (100, 100)


def _standing(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Tall box (aspect 10/20 = 0.5) ⇒ upright.
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 20, x2=foot_x + 5, y2=foot_y)


def _fallen(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Wide box (aspect 40/10 = 4.0) ⇒ collapsed.
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 20, y1=foot_y - 10, x2=foot_x + 20, y2=foot_y)


def _camera(with_zone: bool = False) -> CameraConfig:
    zones = ([Zone(id="ward", polygon=[
        Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
        Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])] if with_zone else [])
    return CameraConfig(id="floor", source="x", mode=Mode.fall, zones=zones)


def _alerts(events):
    return [e for e in events if e.type == EventType.fall_alert]


def test_standing_person_no_alert():
    c = FallCounter("t", "s", _camera())
    c.update([_standing(1)], FRAME, ts=0.0)
    out = c.update([_standing(1)], FRAME, ts=_FALL_CONFIRM_SECONDS + 5.0)
    assert _alerts(out) == []


def test_fall_before_confirm_window_no_alert():
    c = FallCounter("t", "s", _camera())
    out = c.update([_fallen(1)], FRAME, ts=0.0)
    assert _alerts(out) == []


def test_fall_crossing_confirm_emits_one_alert():
    c = FallCounter("t", "s", _camera())
    c.update([_fallen(1)], FRAME, ts=0.0)
    out = c.update([_fallen(1)], FRAME, ts=_FALL_CONFIRM_SECONDS)
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].track_id == 1
    assert alerts[0].count == 1
    assert alerts[0].labels == [f"{int(_FALL_CONFIRM_SECONDS)}s"]


def test_debounced_while_still_down():
    c = FallCounter("t", "s", _camera())
    c.update([_fallen(1)], FRAME, ts=0.0)
    c.update([_fallen(1)], FRAME, ts=_FALL_CONFIRM_SECONDS)       # first (only) alert
    again = c.update([_fallen(1)], FRAME, ts=_FALL_CONFIRM_SECONDS + 3.0)
    assert _alerts(again) == []


def test_rearms_after_standing_then_falling_again():
    c = FallCounter("t", "s", _camera())
    c.update([_fallen(1)], FRAME, ts=0.0)
    c.update([_fallen(1)], FRAME, ts=_FALL_CONFIRM_SECONDS)        # alert #1
    c.update([_standing(1)], FRAME, ts=_FALL_CONFIRM_SECONDS + 1)  # stood up ⇒ re-arm
    c.update([_fallen(1)], FRAME, ts=_FALL_CONFIRM_SECONDS + 2)    # falls again
    out = c.update([_fallen(1)], FRAME, ts=_FALL_CONFIRM_SECONDS * 2 + 2)
    assert len(_alerts(out)) == 1


def test_fall_outside_zone_no_alert():
    c = FallCounter("t", "s", _camera(with_zone=True))
    # Collapsed in a corner, outside the 0.3..0.7 zone.
    c.update([_fallen(1, foot_x=12, foot_y=12)], FRAME, ts=0.0)
    out = c.update([_fallen(1, foot_x=12, foot_y=12)], FRAME, ts=_FALL_CONFIRM_SECONDS + 1)
    assert _alerts(out) == []
