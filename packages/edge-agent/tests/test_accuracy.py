"""Regression tests for the accuracy harness itself.

These use the detections-fixture mode ONLY: no video, no YOLO, no torch/opencv,
so they run under the plain test venv. They prove two things at once:

1. the harness tallies emitted events into the right metrics, and
2. its error math (abs / %-error) and ``assert_within`` behave correctly.

The synthetic detections are crafted to exercise the real counters (line
crossing, parking zone occupancy, warehouse count deltas) end to end.
"""

from __future__ import annotations

import pytest

from camai_schema import Mode, ObjectClass

from camai_edge.config import Line, Point, Zone
from camai_edge.accuracy import (
    ARRIVALS,
    DEPARTURES,
    ENTRIES,
    EXITS,
    NET_DELTA,
    OCCUPANCY,
    AccuracyRunner,
    FrameDetections,
    ReferenceClip,
    object_at,
    person_at,
    tally_metrics,
    vehicle_at,
    zone_key,
)

FRAME = (100, 100)


# ---------------------------------------------------------------------------
# Retail: line crossing
# ---------------------------------------------------------------------------
def _retail_clip(ground_truth: dict[str, float]) -> ReferenceClip:
    return ReferenceClip(
        name="retail-door",
        mode=Mode.retail,
        frame_size=FRAME,
        lines=[Line(id="door", a=Point(x=0.15, y=0.5), b=Point(x=0.85, y=0.5))],
        ground_truth=ground_truth,
    )


def _retail_fixture() -> list[FrameDetections]:
    # ts=0 establishes each track's starting side of the line (pixel y=50):
    #   tracks 1 & 2 below (y=60), track 3 above (y=40).
    # ts=1 crosses: 1 & 2 move above -> two entries; 3 moves below -> one exit.
    return [
        FrameDetections(ts=0.0, detections=[
            person_at(1, foot_x=50, foot_y=60),
            person_at(2, foot_x=50, foot_y=60),
            person_at(3, foot_x=50, foot_y=40),
        ]),
        FrameDetections(ts=1.0, detections=[
            person_at(1, foot_x=50, foot_y=40),
            person_at(2, foot_x=50, foot_y=40),
            person_at(3, foot_x=50, foot_y=60),
        ]),
    ]


def test_retail_exact_counts_zero_error():
    clip = _retail_clip({ENTRIES: 2, EXITS: 1, OCCUPANCY: 1})
    result = AccuracyRunner(clip).run_fixture(_retail_fixture())

    assert result.metrics[ENTRIES].actual == 2
    assert result.metrics[EXITS].actual == 1
    assert result.metrics[OCCUPANCY].actual == 1
    # Exact match -> no error anywhere.
    assert result.max_pct_error() == 0.0
    result.assert_within(0.0)  # must not raise


def test_retail_percent_error_is_computed():
    # Ground truth claims 4 entries; the harness measures 2 -> 50% error.
    clip = _retail_clip({ENTRIES: 4})
    result = AccuracyRunner(clip).run_fixture(_retail_fixture())

    m = result.metrics[ENTRIES]
    assert m.actual == 2
    assert m.abs_error == 2
    assert m.pct_error == pytest.approx(50.0)
    # 50% error must fail a 10% tolerance but pass a 60% one.
    with pytest.raises(AssertionError):
        result.assert_within(10.0)
    result.assert_within(60.0)


def test_within_tolerance_band_passes():
    # Measured 2 vs expected 2 within a nonzero band, and a deliberately loose
    # abs tolerance rescues a small-count metric.
    clip = _retail_clip({ENTRIES: 2, EXITS: 2})  # exits truth off by one
    result = AccuracyRunner(clip).run_fixture(_retail_fixture())
    # exits: expected 2, actual 1 -> 50% error, abs 1. Fails on pct alone...
    with pytest.raises(AssertionError):
        result.assert_within(10.0)
    # ...but passes when one miscount is tolerated absolutely.
    result.assert_within(10.0, abs_tolerance=1)


# ---------------------------------------------------------------------------
# Parking: zone occupancy transitions
# ---------------------------------------------------------------------------
def _parking_clip(ground_truth: dict[str, float]) -> ReferenceClip:
    return ReferenceClip(
        name="lot-space-1",
        mode=Mode.parking,
        frame_size=FRAME,
        zones=[Zone(id="space-1", polygon=[
            Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
            Point(x=0.7, y=0.7), Point(x=0.3, y=0.7),
        ])],
        ground_truth=ground_truth,
    )


def test_parking_arrival_and_departure():
    # A vehicle parks (held 3 frames -> commit) then leaves (held 3 frames).
    # ts jumps past the 15s sample interval at the end to also emit a per-zone
    # occupancy_sample, which should read 0 (empty) by then.
    veh = vehicle_at(1, foot_x=50, foot_y=50)  # centre of the zone
    empty = FrameDetections  # alias for readability below
    frames = [
        FrameDetections(ts=0.0, detections=[veh]),
        FrameDetections(ts=1.0, detections=[veh]),
        FrameDetections(ts=2.0, detections=[veh]),   # commit -> vehicle_parked
        empty(ts=3.0, detections=[]),
        empty(ts=4.0, detections=[]),
        empty(ts=5.0, detections=[]),                # commit -> vehicle_left
        empty(ts=20.0, detections=[]),               # >15s -> occupancy_sample=0
    ]
    clip = _parking_clip({
        ARRIVALS: 1,
        DEPARTURES: 1,
        zone_key("space-1", ARRIVALS): 1,
        zone_key("space-1", OCCUPANCY): 0,
    })
    result = AccuracyRunner(clip).run_fixture(frames)

    assert result.metrics[ARRIVALS].actual == 1
    assert result.metrics[DEPARTURES].actual == 1
    assert result.metrics[zone_key("space-1", ARRIVALS)].actual == 1
    assert result.metrics[zone_key("space-1", OCCUPANCY)].actual == 0
    result.assert_within(0.0)


def test_parking_vehicle_outside_zone_scores_zero():
    outside = vehicle_at(1, foot_x=10, foot_y=10)  # top-left, outside polygon
    frames = [FrameDetections(ts=float(t), detections=[outside]) for t in range(5)]
    clip = _parking_clip({ARRIVALS: 0})
    result = AccuracyRunner(clip).run_fixture(frames)

    assert result.metrics[ARRIVALS].actual == 0
    # Expected 0, actual 0 -> 0% error, not inf.
    assert result.metrics[ARRIVALS].pct_error == 0.0
    result.assert_within(0.0)


# ---------------------------------------------------------------------------
# Warehouse: signed count deltas
# ---------------------------------------------------------------------------
def test_warehouse_net_delta():
    clip = ReferenceClip(
        name="bay-A",
        mode=Mode.warehouse,
        frame_size=FRAME,
        zones=[Zone(id="bay", polygon=[
            Point(x=0.2, y=0.2), Point(x=0.8, y=0.2),
            Point(x=0.8, y=0.8), Point(x=0.2, y=0.8),
        ])],
        ground_truth={NET_DELTA: 2, zone_key("bay", NET_DELTA): 2},
    )
    # Two people inside the bay, held long enough to commit -> count_delta +2.
    people = [person_at(1, foot_x=40, foot_y=50), person_at(2, foot_x=60, foot_y=50)]
    frames = [FrameDetections(ts=float(t), detections=people) for t in range(3)]
    result = AccuracyRunner(clip).run_fixture(frames)

    assert result.metrics[NET_DELTA].actual == 2
    assert result.metrics[zone_key("bay", NET_DELTA)].actual == 2
    result.assert_within(0.0)


# ---------------------------------------------------------------------------
# Tally + reporting helpers
# ---------------------------------------------------------------------------
def test_tally_metrics_directly_from_events():
    clip = _retail_clip({})
    runner = AccuracyRunner(clip)
    result = runner.run_fixture(_retail_fixture())
    # total_events counts everything the counter emitted (entries + exits here).
    assert result.total_events >= 3

    # tally_metrics is the public reducer; feeding it the same events reproduces
    # the derived retail occupancy.
    # (Rebuild events via a fresh run to keep the assertion self-contained.)
    from camai_edge.counting import make_counter

    counter = make_counter("t", "s", clip.camera())
    events = []
    for f in _retail_fixture():
        events.extend(counter.update(f.detections, FRAME, f.ts))
    metrics = tally_metrics(events, Mode.retail)
    assert metrics[ENTRIES] == 2
    assert metrics[EXITS] == 1
    assert metrics[OCCUPANCY] == 1


def test_format_table_is_readable():
    clip = _retail_clip({ENTRIES: 2, EXITS: 1})
    result = AccuracyRunner(clip).run_fixture(_retail_fixture())
    table = result.format_table()
    assert "retail-door" in table
    assert "entries" in table
    assert "max error" in table


def test_run_video_requires_a_path():
    clip = _retail_clip({ENTRIES: 2})
    with pytest.raises(ValueError):
        AccuracyRunner(clip).run_video()
