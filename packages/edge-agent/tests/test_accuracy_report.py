"""Regression tests for the cross-vertical accuracy report.

Two layers:
1. the extended ``tally_metrics`` reduces each newer event type (PPE, traffic,
   capacity, proximity, fire, thermal, dwell) into the right flat metric, and
2. the published suite scores exactly and renders to markdown/JSON.

All fixture-mode: no video, no YOLO, no torch/opencv.
"""

from __future__ import annotations

import json

import pytest

from camai_schema import Event, EventType, Mode

from camai_edge.accuracy import (
    CAPACITY_BREACHES,
    DWELL_COUNT,
    HAZARD_ALERTS,
    MEAN_DWELL_SECONDS,
    OVERHEAT_ALERTS,
    PPE_VIOLATIONS,
    PROXIMITY_ALERTS,
    VEHICLE_CROSSINGS,
    tally_metrics,
    zone_key,
)
from camai_edge.accuracy_report import (
    default_fixture_suite,
    report_json,
    report_markdown,
    report_rows,
    run_suite,
)


def _event(etype: EventType, mode: Mode, **kwargs) -> Event:
    return Event(
        tenant_id="t", site_id="s", camera_id="c",
        type=etype, mode=mode, **kwargs,
    )


# ---------------------------------------------------------------------------
# Extended tally: one assertion per new event type
# ---------------------------------------------------------------------------
def test_tally_ppe_violations():
    events = [
        _event(EventType.ppe_violation, Mode.safety, zone_id="dock"),
        _event(EventType.ppe_violation, Mode.safety, zone_id="dock"),
        _event(EventType.ppe_violation, Mode.safety, zone_id="line"),
    ]
    m = tally_metrics(events, Mode.safety)
    assert m[PPE_VIOLATIONS] == 3
    assert m[zone_key("dock", PPE_VIOLATIONS)] == 2
    assert m[zone_key("line", PPE_VIOLATIONS)] == 1


def test_tally_vehicle_crossings():
    # Traffic crossings carry a line, not a zone -> only the global metric moves.
    events = [
        _event(EventType.vehicle_crossing, Mode.traffic, line_id="cordon"),
        _event(EventType.vehicle_crossing, Mode.traffic, line_id="cordon"),
    ]
    m = tally_metrics(events, Mode.traffic)
    assert m[VEHICLE_CROSSINGS] == 2


def test_tally_capacity_breaches():
    events = [_event(EventType.capacity_breach, Mode.capacity, zone_id="hall")]
    m = tally_metrics(events, Mode.capacity)
    assert m[CAPACITY_BREACHES] == 1
    assert m[zone_key("hall", CAPACITY_BREACHES)] == 1


def test_tally_proximity_alerts():
    events = [
        _event(EventType.proximity_alert, Mode.proximity),
        _event(EventType.proximity_alert, Mode.proximity),
    ]
    m = tally_metrics(events, Mode.proximity)
    assert m[PROXIMITY_ALERTS] == 2


def test_tally_hazard_alerts():
    events = [
        _event(EventType.hazard_alert, Mode.fire, zone_id="floor"),
        _event(EventType.hazard_alert, Mode.fire),  # scene-scope, no zone
    ]
    m = tally_metrics(events, Mode.fire)
    assert m[HAZARD_ALERTS] == 2
    assert m[zone_key("floor", HAZARD_ALERTS)] == 1


def test_tally_overheat_alerts():
    events = [_event(EventType.overheat_alert, Mode.thermal, zone_id="lane")]
    m = tally_metrics(events, Mode.thermal)
    assert m[OVERHEAT_ALERTS] == 1
    assert m[zone_key("lane", OVERHEAT_ALERTS)] == 1


def test_tally_dwell_count_and_mean():
    events = [
        _event(EventType.dwell, Mode.queue, zone_id="lane", dwell_seconds=1.0),
        _event(EventType.dwell, Mode.queue, zone_id="lane", dwell_seconds=4.0),
    ]
    m = tally_metrics(events, Mode.queue)
    assert m[DWELL_COUNT] == 2
    assert m[MEAN_DWELL_SECONDS] == pytest.approx(2.5)
    assert m[zone_key("lane", DWELL_COUNT)] == 2
    assert m[zone_key("lane", MEAN_DWELL_SECONDS)] == pytest.approx(2.5)


def test_tally_leaves_existing_keys_untouched():
    # A dwell event must not perturb entry/exit accounting.
    events = [
        _event(EventType.entry, Mode.retail),
        _event(EventType.dwell, Mode.queue, zone_id="lane", dwell_seconds=3.0),
    ]
    m = tally_metrics(events, Mode.retail)
    assert m["entries"] == 1
    assert m[DWELL_COUNT] == 1


# ---------------------------------------------------------------------------
# The published suite scores exactly
# ---------------------------------------------------------------------------
def test_suite_scores_within_tolerance():
    results = run_suite(default_fixture_suite())
    assert len(results) >= 7  # at least the seven headline verticals
    for r in results:
        # Small integer counts: a generous abs tolerance guards against a %-blowup
        # on a near-zero metric, but these fixtures are built to hit truth exactly.
        r.assert_within(0.0, abs_tolerance=0)
        assert r.max_pct_error() == 0.0


def test_suite_covers_the_core_verticals():
    modes = {r.mode for r in run_suite(default_fixture_suite())}
    for expected in (
        Mode.retail, Mode.parking, Mode.staffing, Mode.queue,
        Mode.safety, Mode.traffic, Mode.capacity,
    ):
        assert expected in modes


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def test_report_markdown_lists_every_clip():
    suite = default_fixture_suite()
    results = run_suite(suite)
    md = report_markdown(results)
    assert md.strip()
    assert "| Clip | Mode |" in md
    assert "Overall:" in md
    for clip, _frames in suite:
        assert clip.name in md


def test_report_json_round_trips():
    results = run_suite(default_fixture_suite())
    payload = report_json(results)
    text = json.dumps(payload)  # must not raise
    restored = json.loads(text)
    assert restored["summary"]["clips"] == len(results)
    assert restored["summary"]["max_pct_error"] == 0.0
    assert len(restored["clips"]) == len(results)


def test_report_rows_shape():
    rows = report_rows(run_suite(default_fixture_suite()))
    for row in rows:
        assert set(row) == {"clip", "mode", "max_pct_error", "mean_pct_error", "total_events"}
