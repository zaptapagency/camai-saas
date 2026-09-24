"""Regression tests for the counting logic.

These deliberately avoid importing the detector, so they run fast and with no
torch/ultralytics install — they test the geometry + counting rules, which is
where accuracy bugs actually live.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Line, Point, Zone
from camai_edge.counting.line_crossing import LineCrossingCounter
from camai_edge.counting.zone_occupancy import ZoneOccupancyCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_y: float, foot_x: float = 50.0) -> Detection:
    return Detection(
        track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
        x1=foot_x - 5, y1=foot_y - 20, x2=foot_x + 5, y2=foot_y,
    )


def _vehicle(track_id: int, foot_x: float, foot_y: float) -> Detection:
    return Detection(
        track_id=track_id, object_class=ObjectClass.vehicle, confidence=0.9,
        x1=foot_x - 10, y1=foot_y - 20, x2=foot_x + 10, y2=foot_y,
    )


def _retail_camera() -> CameraConfig:
    return CameraConfig(
        id="cam", source="x", mode=Mode.retail,
        lines=[Line(id="door", a=Point(x=0.15, y=0.5), b=Point(x=0.85, y=0.5))],
    )


def test_line_crossing_counts_one_entry():
    counter = LineCrossingCounter("t", "s", _retail_camera())
    # Establish starting side (below the line, y=60), then cross above (y=40).
    assert counter.update([_person(1, foot_y=60)], FRAME, ts=0.0) == []
    events = counter.update([_person(1, foot_y=40)], FRAME, ts=1.0)
    entries = [e for e in events if e.type == EventType.entry]
    assert len(entries) == 1
    assert entries[0].line_id == "door"


def test_line_crossing_invert_flips_direction():
    cam = _retail_camera()
    cam.lines[0].invert = True
    counter = LineCrossingCounter("t", "s", cam)
    counter.update([_person(1, foot_y=60)], FRAME, ts=0.0)
    events = counter.update([_person(1, foot_y=40)], FRAME, ts=1.0)
    assert [e.type for e in events if e.type in (EventType.entry, EventType.exit)] == [EventType.exit]


def test_no_crossing_no_event():
    counter = LineCrossingCounter("t", "s", _retail_camera())
    counter.update([_person(1, foot_y=60)], FRAME, ts=0.0)
    # Moves but stays on the same side of the line.
    events = counter.update([_person(1, foot_y=70)], FRAME, ts=1.0)
    assert [e for e in events if e.type in (EventType.entry, EventType.exit)] == []


def _retail_camera_with_zone() -> CameraConfig:
    """Retail camera with a door line *and* a dwell zone over the middle of frame."""
    cam = _retail_camera()
    cam.zones = [Zone(id="produce", polygon=[
        Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
        Point(x=0.7, y=0.7), Point(x=0.3, y=0.7),
    ])]
    return cam


def test_retail_dwell_emitted_on_leave():
    counter = LineCrossingCounter("t", "s", _retail_camera_with_zone())
    inside = _person(1, foot_x=50, foot_y=50)   # centre of the produce zone
    outside = _person(1, foot_x=10, foot_y=10)  # top-left corner, outside the zone

    # Enter the zone at t=0, still there at t=5 → no dwell event yet.
    assert counter.update([inside], FRAME, ts=0.0) == []
    assert [e for e in counter.update([inside], FRAME, ts=5.0)
            if e.type == EventType.dwell] == []

    # Leaves the zone; grace is 2s, so at t=8 (>5+2) the dwell fires.
    counter.update([outside], FRAME, ts=6.0)          # first frame outside (within grace)
    events = counter.update([outside], FRAME, ts=8.0)  # grace elapsed
    dwell = [e for e in events if e.type == EventType.dwell]
    assert len(dwell) == 1
    assert dwell[0].zone_id == "produce"
    assert dwell[0].object_class == ObjectClass.person
    assert dwell[0].track_id == 1
    assert dwell[0].dwell_seconds == 5.0  # last_seen (t=5) - enter (t=0)


def test_retail_dwell_grace_absorbs_brief_occlusion():
    counter = LineCrossingCounter("t", "s", _retail_camera_with_zone())
    inside = _person(1, foot_x=50, foot_y=50)
    outside = _person(1, foot_x=10, foot_y=10)

    counter.update([inside], FRAME, ts=0.0)
    counter.update([outside], FRAME, ts=1.0)  # one-frame miss, still within grace
    events = counter.update([inside], FRAME, ts=2.0)  # back inside → no leave
    assert [e for e in events if e.type == EventType.dwell] == []
    # The visit is still one continuous presence, not two.
    assert list(counter._in_zone["produce"].keys()) == [1]


def test_retail_without_zones_emits_no_dwell():
    counter = LineCrossingCounter("t", "s", _retail_camera())
    for ts in range(5):
        events = counter.update([_person(1, foot_y=50)], FRAME, ts=float(ts))
        assert all(e.type != EventType.dwell for e in events)


def _parking_camera() -> CameraConfig:
    return CameraConfig(
        id="lot", source="x", mode=Mode.parking,
        zones=[Zone(id="space-1", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7),
        ])],
    )


def test_parking_debounce_then_parked_event():
    counter = ZoneOccupancyCounter("t", "s", _parking_camera())
    veh = _vehicle(1, foot_x=50, foot_y=50)  # centre of the zone
    # Debounce is 3 samples: first two produce nothing, the third commits.
    assert counter.update([veh], FRAME, ts=0.0) == []
    assert counter.update([veh], FRAME, ts=1.0) == []
    events = counter.update([veh], FRAME, ts=2.0)
    parked = [e for e in events if e.type == EventType.vehicle_parked]
    assert len(parked) == 1
    assert parked[0].zone_id == "space-1"


def test_vehicle_outside_zone_not_counted():
    counter = ZoneOccupancyCounter("t", "s", _parking_camera())
    outside = _vehicle(1, foot_x=10, foot_y=10)  # top-left corner, outside polygon
    for ts in range(4):
        events = counter.update([outside], FRAME, ts=float(ts))
        assert all(e.type != EventType.vehicle_parked for e in events)
