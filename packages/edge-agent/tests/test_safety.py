"""Regression tests for the PPE/safety counter.

Fixture-based: crafts person + PPE-item Detections directly (as a PPE-trained
detector would emit), so it needs no torch/opencv and no PPE model.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.safety import SafetyCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Box spans x±5 around foot_x, y from foot_y-30 to foot_y (head to feet).
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y)


def _ppe(obj: ObjectClass, cx: float, cy: float) -> Detection:
    return Detection(track_id=None, object_class=obj, confidence=0.9,
                     x1=cx - 3, y1=cy - 3, x2=cx + 3, y2=cy + 3)


def _camera() -> CameraConfig:
    return CameraConfig(
        id="dock", source="x", mode=Mode.safety,
        zones=[Zone(id="hazard", required_ppe=["helmet", "vest"], polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])],
    )


def test_compliant_person_no_violation():
    c = SafetyCounter("t", "s", _camera())
    dets = [_person(1), _ppe(ObjectClass.helmet, 50, 25), _ppe(ObjectClass.vest, 50, 40)]
    events = c.update(dets, FRAME, ts=0.0)
    assert [e for e in events if e.type == EventType.ppe_violation] == []


def test_missing_one_item_flagged_with_label():
    c = SafetyCounter("t", "s", _camera())
    dets = [_person(1), _ppe(ObjectClass.helmet, 50, 25)]   # vest missing
    events = c.update(dets, FRAME, ts=0.0)
    v = [e for e in events if e.type == EventType.ppe_violation]
    assert len(v) == 1
    assert v[0].labels == ["vest"]
    assert v[0].count == 1
    assert v[0].zone_id == "hazard"
    assert v[0].track_id == 1


def test_no_ppe_flags_all_required():
    c = SafetyCounter("t", "s", _camera())
    events = c.update([_person(1)], FRAME, ts=0.0)
    v = [e for e in events if e.type == EventType.ppe_violation]
    assert len(v) == 1 and v[0].labels == ["helmet", "vest"] and v[0].count == 2


def test_violation_debounced_until_state_changes():
    c = SafetyCounter("t", "s", _camera())
    p = _person(1)
    first = c.update([p], FRAME, ts=0.0)      # missing both -> emits
    second = c.update([p], FRAME, ts=0.5)     # still missing both -> no new event
    assert len([e for e in first if e.type == EventType.ppe_violation]) == 1
    assert [e for e in second if e.type == EventType.ppe_violation] == []
    # Now they put on a helmet: missing set shrinks to [vest] -> re-fires.
    third = c.update([p, _ppe(ObjectClass.helmet, 50, 25)], FRAME, ts=1.0)
    v = [e for e in third if e.type == EventType.ppe_violation]
    assert len(v) == 1 and v[0].labels == ["vest"]


def test_person_outside_zone_not_checked():
    c = SafetyCounter("t", "s", _camera())
    events = c.update([_person(1, foot_x=10, foot_y=10)], FRAME, ts=0.0)  # outside
    assert [e for e in events if e.type == EventType.ppe_violation] == []


def test_headcount_sample_emitted():
    c = SafetyCounter("t", "s", _camera())
    people = [_person(1, 40, 50), _person(2, 60, 50)]
    c.update(people, FRAME, ts=0.0)
    events = c.update(people, FRAME, ts=16.0)   # crosses the 15s sample window
    samples = [e for e in events if e.type == EventType.occupancy_sample]
    assert len(samples) == 1 and samples[0].count == 2 and samples[0].zone_id == "hazard"
