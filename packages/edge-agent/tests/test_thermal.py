"""Regression tests for the thermal fever/overheat counter.

Fixture-based: crafts Detections carrying a ``temperature`` directly (as a thermal
camera's detector would emit), so it needs no torch/opencv and no thermal model.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.thermal import ThermalCounter, _SAMPLE_INTERVAL
from camai_edge.detect import Detection

FRAME = (100, 100)


def _det(track_id, temperature, foot_x=50.0, foot_y=50.0):
    """A thermal detection whose box spans x±5, y from foot_y-30 to foot_y."""
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y,
                     temperature=temperature)


def _camera(zones=None) -> CameraConfig:
    return CameraConfig(id="kiosk", source="x", mode=Mode.thermal,
                        temp_threshold_c=38.0, zones=zones or [])


def _zone_camera() -> CameraConfig:
    return _camera(zones=[Zone(id="lane", polygon=[
        Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
        Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])])


def _alerts(events):
    return [e for e in events if e.type == EventType.overheat_alert]


def _samples(events):
    return [e for e in events if e.type == EventType.occupancy_sample]


def test_below_threshold_no_alert():
    c = ThermalCounter("t", "s", _camera())
    events = c.update([_det(1, 36.5)], FRAME, ts=0.0)
    assert _alerts(events) == []


def test_elevated_emits_single_alert_with_reading():
    c = ThermalCounter("t", "s", _camera())
    events = c.update([_det(1, 39.2)], FRAME, ts=0.0)
    a = _alerts(events)
    assert len(a) == 1
    assert a[0].track_id == 1
    assert a[0].count == 1
    assert a[0].labels == ["39.2C"]
    assert a[0].zone_id is None


def test_alert_debounced_while_still_hot():
    c = ThermalCounter("t", "s", _camera())
    first = c.update([_det(1, 39.2)], FRAME, ts=0.0)
    second = c.update([_det(1, 39.4)], FRAME, ts=0.5)   # still elevated -> no new alert
    assert len(_alerts(first)) == 1
    assert _alerts(second) == []


def test_alert_refires_after_dropping_below():
    c = ThermalCounter("t", "s", _camera())
    first = c.update([_det(1, 39.2)], FRAME, ts=0.0)     # elevated -> alert
    mid = c.update([_det(1, 36.8)], FRAME, ts=1.0)       # cooled -> re-arm, no alert
    again = c.update([_det(1, 39.5)], FRAME, ts=2.0)     # elevated again -> re-fires
    assert len(_alerts(first)) == 1
    assert _alerts(mid) == []
    assert len(_alerts(again)) == 1
    assert _alerts(again)[0].labels == ["39.5C"]


def test_no_temperature_ignored():
    c = ThermalCounter("t", "s", _camera())
    events = c.update([_det(1, None)], FRAME, ts=0.0)
    assert _alerts(events) == []
    # And it is not counted as elevated in the periodic sample.
    events = c.update([_det(1, None)], FRAME, ts=16.0)
    s = _samples(events)
    assert len(s) == 1 and s[0].count == 0


def test_periodic_sample_counts_elevated():
    c = ThermalCounter("t", "s", _camera())
    dets = [_det(1, 39.2, foot_x=40), _det(2, 38.5, foot_x=60), _det(3, 36.0, foot_x=50)]
    c.update(dets, FRAME, ts=0.0)                        # inside sample window -> no sample
    events = c.update(dets, FRAME, ts=_SAMPLE_INTERVAL + 1.0)
    s = _samples(events)
    assert len(s) == 1
    assert s[0].count == 2       # tracks 1 and 2 are at/above 38.0
    assert s[0].zone_id is None
    assert s[0].labels is None


def test_elevated_outside_zone_ignored():
    c = ThermalCounter("t", "s", _zone_camera())
    # Foot point (10, 10) is outside the [0.3,0.7] zone -> out of scope.
    events = c.update([_det(1, 39.9, foot_x=10, foot_y=10)], FRAME, ts=0.0)
    assert _alerts(events) == []
    # An elevated detection inside the zone is flagged and tagged with the zone id.
    events = c.update([_det(2, 39.9, foot_x=50, foot_y=50)], FRAME, ts=1.0)
    a = _alerts(events)
    assert len(a) == 1 and a[0].zone_id == "lane"
