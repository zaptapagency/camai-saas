"""Regression tests for the loitering counter."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.loitering import LoiteringCounter, _LOITER_SECONDS
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 20, x2=foot_x + 5, y2=foot_y)


def _camera() -> CameraConfig:
    return CameraConfig(
        id="lobby", source="x", mode=Mode.loitering,
        zones=[Zone(id="entrance", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def _alerts(events):
    return [e for e in events if e.type == EventType.loitering_alert]


def _samples(events):
    return [e for e in events if e.type == EventType.occupancy_sample]


def test_below_threshold_no_alert():
    c = LoiteringCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)
    out = c.update([_person(1)], FRAME, ts=_LOITER_SECONDS - 1.0)
    assert _alerts(out) == []


def test_crossing_threshold_emits_one_alert():
    c = LoiteringCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)
    out = c.update([_person(1)], FRAME, ts=_LOITER_SECONDS)
    alerts = _alerts(out)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.zone_id == "entrance"
    assert a.track_id == 1
    assert a.count == 1
    assert a.labels == [f"{int(_LOITER_SECONDS)}s"]


def test_debounced_while_staying():
    c = LoiteringCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)
    c.update([_person(1)], FRAME, ts=_LOITER_SECONDS)       # first (only) alert
    again = c.update([_person(1)], FRAME, ts=_LOITER_SECONDS + 5.0)
    assert _alerts(again) == []


def test_person_outside_zone_no_alert():
    c = LoiteringCounter("t", "s", _camera())
    # Foot point well outside the 0.3..0.7 zone.
    c.update([_person(1, foot_x=5.0, foot_y=5.0)], FRAME, ts=0.0)
    out = c.update([_person(1, foot_x=5.0, foot_y=5.0)], FRAME, ts=_LOITER_SECONDS + 10.0)
    assert _alerts(out) == []


def test_occupancy_sample_emitted_with_count():
    c = LoiteringCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)
    out = c.update([_person(1)], FRAME, ts=16.0)  # past the 15s sample interval
    samples = _samples(out)
    assert len(samples) == 1
    assert samples[0].zone_id == "entrance"
    assert samples[0].count == 1
