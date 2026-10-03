"""Regression tests for the weapon-detection counter."""

from camai_schema import EventType, Mode, ObjectClass

from camai_edge.config import CameraConfig, Point, Zone
from camai_edge.counting.weapon import WeaponCounter, _CLEAR_GRACE_SECONDS
from camai_edge.detect import Detection

FRAME = (100, 100)


def _weapon(cls: ObjectClass = ObjectClass.gun, cx: float = 50.0, cy: float = 50.0) -> Detection:
    return Detection(track_id=None, object_class=cls, confidence=0.9,
                     x1=cx - 5, y1=cy - 5, x2=cx + 5, y2=cy + 5)


def _scene_camera() -> CameraConfig:
    return CameraConfig(id="lobby", source="x", mode=Mode.weapon)


def _zone_camera() -> CameraConfig:
    return CameraConfig(
        id="lobby", source="x", mode=Mode.weapon,
        zones=[Zone(id="counter", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7)])])


def _alerts(events):
    return [e for e in events if e.type == EventType.weapon_alert]


def test_weapon_appears_emits_one_alert():
    c = WeaponCounter("t", "s", _scene_camera())
    out = c.update([_weapon(ObjectClass.gun)], FRAME, ts=0.0)
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].labels == ["gun"]
    assert alerts[0].count == 1


def test_no_weapon_no_alert():
    c = WeaponCounter("t", "s", _scene_camera())
    person = Detection(track_id=1, object_class=ObjectClass.person, confidence=0.9,
                       x1=45, y1=30, x2=55, y2=50)
    assert _alerts(c.update([person], FRAME, ts=0.0)) == []


def test_debounced_while_weapon_persists():
    c = WeaponCounter("t", "s", _scene_camera())
    c.update([_weapon()], FRAME, ts=0.0)
    again = c.update([_weapon()], FRAME, ts=1.0)
    assert _alerts(again) == []


def test_refires_when_weapon_type_set_changes():
    c = WeaponCounter("t", "s", _scene_camera())
    c.update([_weapon(ObjectClass.knife)], FRAME, ts=0.0)            # knife
    out = c.update([_weapon(ObjectClass.knife), _weapon(ObjectClass.gun)], FRAME, ts=1.0)
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].labels == ["gun", "knife"]


def test_rearms_after_clear_grace():
    c = WeaponCounter("t", "s", _scene_camera())
    c.update([_weapon()], FRAME, ts=0.0)                 # alert
    c.update([], FRAME, ts=1.0)                          # clear — start grace
    c.update([], FRAME, ts=_CLEAR_GRACE_SECONDS + 1.0)   # grace elapsed — re-arm
    out = c.update([_weapon()], FRAME, ts=_CLEAR_GRACE_SECONDS + 2.0)
    assert len(_alerts(out)) == 1


def test_weapon_outside_zone_no_alert():
    c = WeaponCounter("t", "s", _zone_camera())
    out = c.update([_weapon(cx=10, cy=10)], FRAME, ts=0.0)  # outside the 0.3..0.7 zone
    assert _alerts(out) == []


def test_weapon_inside_zone_carries_zone_id():
    c = WeaponCounter("t", "s", _zone_camera())
    out = c.update([_weapon(cx=50, cy=50)], FRAME, ts=0.0)
    alerts = _alerts(out)
    assert len(alerts) == 1
    assert alerts[0].zone_id == "counter"
