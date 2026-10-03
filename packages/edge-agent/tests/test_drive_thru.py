"""Regression tests for the drive-thru counter (lane occupancy + service time).

Fixture-based: crafts Detection objects directly, so it runs with no torch/opencv.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.drive_thru import DriveThruCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _vehicle(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.vehicle, confidence=0.9,
                     x1=foot_x - 10, y1=foot_y - 20, x2=foot_x + 10, y2=foot_y)


def _camera() -> CameraConfig:
    return CameraConfig(
        id="lane-cam", source="x", mode=Mode.drive_thru,
        zones=[Zone(id="lane1", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def test_service_time_emitted_on_leave():
    c = DriveThruCounter("t", "s", _camera())
    assert c.update([_vehicle(1)], FRAME, ts=0.0) == []      # enters lane
    assert c.update([_vehicle(1)], FRAME, ts=10.0) == []     # still being served
    events = c.update([], FRAME, ts=20.0)                    # gone > grace (5s) -> left
    dwell = [e for e in events if e.type == EventType.dwell]
    assert len(dwell) == 1
    assert dwell[0].zone_id == "lane1"
    assert dwell[0].track_id == 1
    assert dwell[0].mode == Mode.drive_thru
    assert dwell[0].object_class == ObjectClass.vehicle
    assert dwell[0].dwell_seconds == 10.0                    # last_seen(10) - enter(0)


def test_vehicle_still_present_no_dwell():
    c = DriveThruCounter("t", "s", _camera())
    c.update([_vehicle(1)], FRAME, ts=0.0)
    events = c.update([_vehicle(1)], FRAME, ts=8.0)          # still in lane
    assert [e for e in events if e.type == EventType.dwell] == []


def test_grace_period_absorbs_brief_dropout():
    c = DriveThruCounter("t", "s", _camera())
    c.update([_vehicle(1)], FRAME, ts=0.0)
    c.update([], FRAME, ts=2.0)                              # missed 1 frame (< grace)
    events = c.update([_vehicle(1)], FRAME, ts=4.0)          # reappears
    # No dwell yet — the vehicle never "left"; its service time is continuous.
    assert [e for e in events if e.type == EventType.dwell] == []


def test_lane_occupancy_sample():
    c = DriveThruCounter("t", "s", _camera())
    two = [_vehicle(1, 40, 50), _vehicle(2, 60, 50)]
    c.update(two, FRAME, ts=0.0)                             # before first sample window
    events = c.update(two, FRAME, ts=16.0)                  # crosses 15s sample interval
    samples = [e for e in events if e.type == EventType.occupancy_sample]
    assert len(samples) == 1
    assert samples[0].zone_id == "lane1"
    assert samples[0].count == 2
