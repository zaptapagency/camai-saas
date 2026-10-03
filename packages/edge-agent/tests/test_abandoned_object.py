"""Regression tests for the abandoned-object counter."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.abandoned_object import (
    AbandonedObjectCounter, _ABANDON_SECONDS,
)
from camai_edge.detect import Detection

FRAME = (100, 100)


def _bag(track_id: int, cx: float = 50.0, cy: float = 50.0) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.bag, confidence=0.9,
                     x1=cx - 4, y1=cy - 4, x2=cx + 4, y2=cy + 4)


def _camera(with_zone: bool = False) -> CameraConfig:
    zones = ([Zone(id="hall", polygon=[
        Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
        Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])] if with_zone else [])
    return CameraConfig(id="concourse", source="x", mode=Mode.abandoned_object, zones=zones)


def _alerts(events):
    return [e for e in events if e.type == EventType.abandoned_object_alert]


def test_below_threshold_no_alert():
    c = AbandonedObjectCounter("t", "s", _camera())
    c.update([_bag(1)], FRAME, ts=0.0)
    out = c.update([_bag(1)], FRAME, ts=_ABANDON_SECONDS - 1.0)
    assert _alerts(out) == []


def test_stationary_past_threshold_emits_one_alert():
    c = AbandonedObjectCounter("t", "s", _camera())
    c.update([_bag(1)], FRAME, ts=0.0)
    out = c.update([_bag(1)], FRAME, ts=_ABANDON_SECONDS)
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].track_id == 1
    assert alerts[0].object_class == "bag"
    assert alerts[0].labels == [f"{int(_ABANDON_SECONDS)}s"]


def test_debounced_while_sitting():
    c = AbandonedObjectCounter("t", "s", _camera())
    c.update([_bag(1)], FRAME, ts=0.0)
    c.update([_bag(1)], FRAME, ts=_ABANDON_SECONDS)
    again = c.update([_bag(1)], FRAME, ts=_ABANDON_SECONDS + 5.0)
    assert _alerts(again) == []


def test_moving_object_resets_timer():
    c = AbandonedObjectCounter("t", "s", _camera())
    c.update([_bag(1, cx=20, cy=20)], FRAME, ts=0.0)
    # Jump well beyond the move tolerance just before the threshold ⇒ re-anchor.
    c.update([_bag(1, cx=80, cy=80)], FRAME, ts=_ABANDON_SECONDS - 1.0)
    out = c.update([_bag(1, cx=80, cy=80)], FRAME, ts=_ABANDON_SECONDS)
    assert _alerts(out) == []  # not stationary long enough at the new spot


def test_object_outside_zone_no_alert():
    c = AbandonedObjectCounter("t", "s", _camera(with_zone=True))
    c.update([_bag(1, cx=10, cy=10)], FRAME, ts=0.0)  # outside the 0.3..0.7 zone
    out = c.update([_bag(1, cx=10, cy=10)], FRAME, ts=_ABANDON_SECONDS + 1.0)
    assert _alerts(out) == []


def test_object_inside_zone_carries_zone_id():
    c = AbandonedObjectCounter("t", "s", _camera(with_zone=True))
    c.update([_bag(1, cx=50, cy=50)], FRAME, ts=0.0)
    out = c.update([_bag(1, cx=50, cy=50)], FRAME, ts=_ABANDON_SECONDS)
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].zone_id == "hall"
