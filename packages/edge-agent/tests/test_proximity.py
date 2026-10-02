"""Regression tests for the forklift↔pedestrian proximity counter.

Fixture-based: crafts person + forklift Detections directly (as a warehouse
detector with a forklift class would emit), so it needs no torch/opencv and no
model. FRAME=(100,100) ⇒ diag≈141.4; with proximity_threshold=0.15 the danger
distance is ≈21.2 px.
"""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig
from camai_edge.counting.proximity import ProximityCounter
from camai_edge.detect import Detection

FRAME = (100, 100)


def _person(track_id: int, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    # Box spans x±5 around foot_x, y from foot_y-30 to foot_y (head to feet);
    # foot_point == (foot_x, foot_y).
    return Detection(track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
                     x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y)


def _forklift(foot_x: float, foot_y: float, track_id=None) -> Detection:
    # Box spans x±10 around foot_x, y from foot_y-20 to foot_y; foot_point == (foot_x, foot_y).
    return Detection(track_id=track_id, object_class=ObjectClass.forklift, confidence=0.9,
                     x1=foot_x - 10, y1=foot_y - 20, x2=foot_x + 10, y2=foot_y)


def _camera() -> CameraConfig:
    return CameraConfig(id="aisle", source="x", mode=Mode.proximity,
                        proximity_threshold=0.15)


def _alerts(events):
    return [e for e in events if e.type == EventType.proximity_alert]


def test_person_far_from_forklift_no_alert():
    c = ProximityCounter("t", "s", _camera())
    dets = [_person(1, 10, 50), _forklift(90, 50)]   # 80px apart > ~21.2
    assert _alerts(c.update(dets, FRAME, ts=0.0)) == []


def test_person_within_threshold_alerts_once():
    c = ProximityCounter("t", "s", _camera())
    dets = [_person(1, 50, 50), _forklift(60, 50)]   # 10px apart < ~21.2
    a = _alerts(c.update(dets, FRAME, ts=0.0))
    assert len(a) == 1
    assert a[0].track_id == 1
    assert a[0].count == 1
    assert a[0].zone_id is None
    assert a[0].labels[0].startswith("dist:")


def test_alert_debounced_while_staying_near():
    c = ProximityCounter("t", "s", _camera())
    dets = [_person(1, 50, 50), _forklift(60, 50)]
    first = c.update(dets, FRAME, ts=0.0)
    second = c.update(dets, FRAME, ts=0.5)   # still near -> no new alert
    assert len(_alerts(first)) == 1
    assert _alerts(second) == []


def test_re_fires_after_clearing_grace_then_returning():
    c = ProximityCounter("t", "s", _camera())
    near = [_person(1, 50, 50), _forklift(60, 50)]
    far = [_person(1, 10, 50), _forklift(90, 50)]
    assert len(_alerts(c.update(near, FRAME, ts=0.0))) == 1
    assert _alerts(c.update(far, FRAME, ts=1.0)) == []   # left, still within grace
    assert _alerts(c.update(far, FRAME, ts=4.0)) == []   # grace (>=3s) elapsed -> re-armed
    assert len(_alerts(c.update(near, FRAME, ts=5.0))) == 1  # approaches again -> re-fires


def test_two_forklifts_within_threshold_count_two():
    c = ProximityCounter("t", "s", _camera())
    dets = [_person(1, 50, 50), _forklift(55, 50), _forklift(60, 50)]
    a = _alerts(c.update(dets, FRAME, ts=0.0))
    assert len(a) == 1 and a[0].count == 2


def test_periodic_occupancy_sample_counts_people():
    c = ProximityCounter("t", "s", _camera())
    people = [_person(1, 10, 50), _person(2, 90, 50)]   # both far from any forklift
    c.update(people, FRAME, ts=0.0)                      # no sample yet
    events = c.update(people, FRAME, ts=16.0)            # crosses the 15s window
    samples = [e for e in events if e.type == EventType.occupancy_sample]
    assert len(samples) == 1
    assert samples[0].count == 2
    assert samples[0].zone_id is None
