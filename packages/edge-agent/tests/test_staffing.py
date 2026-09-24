"""Regression tests for the staffing / workstation-coverage counter."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.staffing import StaffingCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 20, x2=foot_x + 5, y2=foot_y)


def _vehicle(track_id: int) -> Detection:
    return Detection(track_id=track_id, object_class=ObjectClass.vehicle, confidence=0.9,
                     x1=45, y1=30, x2=55, y2=50)


def _camera() -> CameraConfig:
    return CameraConfig(
        id="kitchen", source="x", mode=Mode.staffing,
        zones=[Zone(id="grill", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def _samples(events):
    return [e for e in events if e.type == EventType.occupancy_sample]


def test_station_becomes_staffed():
    c = StaffingCounter("t", "s", _camera())
    s = _samples(c.update([_person(1)], FRAME, ts=0.0))
    assert len(s) == 1 and s[0].zone_id == "grill" and s[0].count == 1


def test_station_goes_unstaffed_after_grace():
    c = StaffingCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)          # staffed
    mid = c.update([], FRAME, ts=5.0)              # empty < 10s grace -> no change
    assert _samples(mid) == []
    end = _samples(c.update([], FRAME, ts=12.0))   # empty > grace -> unstaffed
    assert len(end) == 1 and end[0].count == 0


def test_grace_absorbs_brief_absence():
    c = StaffingCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)
    c.update([], FRAME, ts=3.0)                    # stepped out briefly
    back = c.update([_person(1)], FRAME, ts=6.0)   # returned before grace elapsed
    assert _samples(back) == []                    # never flipped to unstaffed


def test_periodic_headcount_sample():
    c = StaffingCounter("t", "s", _camera())
    c.update([_person(1)], FRAME, ts=0.0)          # transition sample
    later = _samples(c.update([_person(1)], FRAME, ts=16.0))  # interval sample
    assert len(later) == 1 and later[0].count == 1


def test_vehicle_does_not_staff_a_station():
    c = StaffingCounter("t", "s", _camera())
    # A vehicle in the zone is not a worker; the station stays unstaffed (no
    # false "staffed" transition).
    events = c.update([_vehicle(1)], FRAME, ts=0.0)
    assert _samples(events) == []
