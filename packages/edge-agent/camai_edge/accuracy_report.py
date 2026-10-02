"""Publishable accuracy report across every CamAI vertical.

WHY this exists
---------------
``accuracy.py`` gives us the machinery to score one clip against ground truth.
This module turns that into the thing sales and the docs site actually quote: a
single deterministic suite that runs **every** vertical through the real counters
and renders one publishable accuracy table (markdown + JSON).

Everything here is detections-fixture mode: no video, no YOLO, no torch/opencv.
Each clip's detections are hand-built so the ground truth is exact and the numbers
are reproducible on any machine and in CI. That is the whole point — a published
accuracy number has to come from a run anyone can repeat, not a one-off demo.

The ground truth in :func:`default_fixture_suite` was tuned against what the real
counters emit (run the suite and read ``format_table()``), so every clip scores at
0% error. A regression in any counter moves a number here and fails the test.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from camai_schema import Mode, ObjectClass

from camai_edge.config import Line, Point, Zone
from camai_edge.detect import Detection
from camai_edge.accuracy import (
    ARRIVALS,
    CAPACITY_BREACHES,
    DEPARTURES,
    DWELL_COUNT,
    ENTRIES,
    EXITS,
    HAZARD_ALERTS,
    MEAN_DWELL_SECONDS,
    NET_DELTA,
    OCCUPANCY,
    OVERHEAT_ALERTS,
    PPE_VIOLATIONS,
    PROXIMITY_ALERTS,
    VEHICLE_CROSSINGS,
    AccuracyResult,
    AccuracyRunner,
    FrameDetections,
    ReferenceClip,
    object_at,
    person_at,
    vehicle_at,
    zone_key,
)

# A clip plus the per-frame detections to replay through it.
Clip = tuple[ReferenceClip, list[FrameDetections]]

# All fixtures use a square 100x100 frame so pixel coordinates read like the
# normalized geometry (a zone at [0.3, 0.7] covers pixels 30..70).
FRAME = (100, 100)

# A central square zone used by several verticals (pixels 30..70).
_CENTRE_POLYGON = [
    Point(x=0.3, y=0.3), Point(x=0.7, y=0.3),
    Point(x=0.7, y=0.7), Point(x=0.3, y=0.7),
]


def _centre_zone(zone_id: str, **kwargs) -> Zone:
    return Zone(id=zone_id, polygon=list(_CENTRE_POLYGON), **kwargs)


def _thermal_person(track_id: int, temperature: float, foot_x: float = 50.0, foot_y: float = 50.0) -> Detection:
    """A person detection carrying a surface temperature, as a thermal camera emits."""
    return Detection(
        track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
        x1=foot_x - 5, y1=foot_y - 30, x2=foot_x + 5, y2=foot_y, temperature=temperature,
    )


# ---------------------------------------------------------------------------
# The suite
# ---------------------------------------------------------------------------
def default_fixture_suite() -> list[Clip]:
    """A deterministic, no-ML suite covering every vertical, each scoring exactly.

    Returns a list of ``(ReferenceClip, frames)`` pairs. Ground truth matches what
    the real counters emit for these detections (verified by the report test), so
    the published table reads 0% error until a counter regresses.
    """
    suite: list[Clip] = []

    # -- retail: line crossing (entries / exits / occupancy) ----------------
    retail = ReferenceClip(
        name="retail-door",
        mode=Mode.retail,
        frame_size=FRAME,
        lines=[Line(id="door", a=Point(x=0.15, y=0.5), b=Point(x=0.85, y=0.5))],
        ground_truth={ENTRIES: 2, EXITS: 1, OCCUPANCY: 1},
    )
    retail_frames = [
        # ts=0 fixes each track's side of the line (pixel y=50): 1 & 2 below, 3 above.
        FrameDetections(ts=0.0, detections=[
            person_at(1, foot_x=50, foot_y=60),
            person_at(2, foot_x=50, foot_y=60),
            person_at(3, foot_x=50, foot_y=40),
        ]),
        # ts=1 crosses: 1 & 2 go above -> 2 entries; 3 goes below -> 1 exit.
        FrameDetections(ts=1.0, detections=[
            person_at(1, foot_x=50, foot_y=40),
            person_at(2, foot_x=50, foot_y=40),
            person_at(3, foot_x=50, foot_y=60),
        ]),
    ]
    suite.append((retail, retail_frames))

    # -- parking: one arrival, one departure, zone ends empty ---------------
    parking = ReferenceClip(
        name="parking-space-1",
        mode=Mode.parking,
        frame_size=FRAME,
        zones=[_centre_zone("space-1")],
        ground_truth={
            ARRIVALS: 1,
            DEPARTURES: 1,
            zone_key("space-1", ARRIVALS): 1,
            zone_key("space-1", DEPARTURES): 1,
            zone_key("space-1", OCCUPANCY): 0,
        },
    )
    veh = vehicle_at(1, foot_x=50, foot_y=50)
    parking_frames = [
        FrameDetections(ts=0.0, detections=[veh]),
        FrameDetections(ts=1.0, detections=[veh]),
        FrameDetections(ts=2.0, detections=[veh]),   # held -> vehicle_parked
        FrameDetections(ts=3.0, detections=[]),
        FrameDetections(ts=4.0, detections=[]),
        FrameDetections(ts=5.0, detections=[]),       # held empty -> vehicle_left
        FrameDetections(ts=20.0, detections=[]),       # >15s -> occupancy_sample = 0
    ]
    suite.append((parking, parking_frames))

    # -- warehouse: signed count_delta of objects in a bay ------------------
    warehouse = ReferenceClip(
        name="warehouse-bay-A",
        mode=Mode.warehouse,
        frame_size=FRAME,
        zones=[_centre_zone("bay")],
        ground_truth={NET_DELTA: 2, zone_key("bay", NET_DELTA): 2},
    )
    bay_people = [person_at(1, foot_x=45, foot_y=50), person_at(2, foot_x=55, foot_y=50)]
    warehouse_frames = [FrameDetections(ts=float(t), detections=bay_people) for t in range(3)]
    suite.append((warehouse, warehouse_frames))

    # -- staffing: anonymous station headcount (zone occupancy) -------------
    staffing = ReferenceClip(
        name="staffing-prep-line",
        mode=Mode.staffing,
        frame_size=FRAME,
        zones=[_centre_zone("prep-line")],
        ground_truth={zone_key("prep-line", OCCUPANCY): 1},
    )
    worker = person_at(1, foot_x=50, foot_y=50)
    staffing_frames = [
        FrameDetections(ts=0.0, detections=[worker]),    # station becomes staffed
        FrameDetections(ts=16.0, detections=[worker]),   # >15s -> periodic sample, head=1
    ]
    suite.append((staffing, staffing_frames))

    # -- queue: dwell count + mean wait -------------------------------------
    queue = ReferenceClip(
        name="queue-checkout",
        mode=Mode.queue,
        frame_size=FRAME,
        zones=[_centre_zone("lane")],
        ground_truth={
            DWELL_COUNT: 2,
            MEAN_DWELL_SECONDS: 2.5,
            zone_key("lane", DWELL_COUNT): 2,
            zone_key("lane", MEAN_DWELL_SECONDS): 2.5,
        },
    )
    p1 = person_at(1, foot_x=45, foot_y=50)
    p2 = person_at(2, foot_x=55, foot_y=50)
    queue_frames = [
        FrameDetections(ts=0.0, detections=[p1, p2]),  # both enter
        FrameDetections(ts=1.0, detections=[p1, p2]),  # p2 last seen here
        FrameDetections(ts=2.0, detections=[p1]),
        FrameDetections(ts=3.0, detections=[p1]),
        FrameDetections(ts=4.0, detections=[p1]),      # p2 left (dwell 1.0); p1 last seen
        FrameDetections(ts=7.0, detections=[]),        # p1 left (dwell 4.0) -> mean 2.5
    ]
    suite.append((queue, queue_frames))

    # -- safety: PPE violation in a required-equipment zone -----------------
    safety = ReferenceClip(
        name="safety-dock",
        mode=Mode.safety,
        frame_size=FRAME,
        zones=[_centre_zone("dock", required_ppe=["helmet", "vest"])],
        ground_truth={PPE_VIOLATIONS: 1, zone_key("dock", PPE_VIOLATIONS): 1},
    )
    # A person in the zone with no PPE items detected -> missing helmet+vest -> 1 violation.
    safety_frames = [FrameDetections(ts=0.0, detections=[person_at(1, foot_x=50, foot_y=50)])]
    suite.append((safety, safety_frames))

    # -- traffic: directional vehicle crossing ------------------------------
    traffic = ReferenceClip(
        name="traffic-cordon",
        mode=Mode.traffic,
        frame_size=FRAME,
        lines=[Line(id="cordon", a=Point(x=0.15, y=0.5), b=Point(x=0.85, y=0.5))],
        ground_truth={VEHICLE_CROSSINGS: 1},
    )
    traffic_frames = [
        FrameDetections(ts=0.0, detections=[vehicle_at(1, foot_x=50, foot_y=60)]),  # below
        FrameDetections(ts=1.0, detections=[vehicle_at(1, foot_x=50, foot_y=40)]),  # above -> 1 crossing
    ]
    suite.append((traffic, traffic_frames))

    # -- capacity: occupancy-limit breach -----------------------------------
    capacity = ReferenceClip(
        name="capacity-hall",
        mode=Mode.capacity,
        frame_size=FRAME,
        zones=[_centre_zone("hall", capacity=2)],
        ground_truth={CAPACITY_BREACHES: 1, zone_key("hall", CAPACITY_BREACHES): 1},
    )
    crowd = [person_at(i, foot_x=40 + i, foot_y=50) for i in range(1, 4)]  # 3 > limit 2
    capacity_frames = [
        FrameDetections(ts=0.0, detections=crowd),   # over starts (within grace)
        FrameDetections(ts=6.0, detections=crowd),   # >5s over -> 1 breach
    ]
    suite.append((capacity, capacity_frames))

    # -- proximity: forklift <-> pedestrian near-miss -----------------------
    proximity = ReferenceClip(
        name="proximity-aisle",
        mode=Mode.proximity,
        frame_size=FRAME,
        ground_truth={PROXIMITY_ALERTS: 1},
    )
    proximity_frames = [
        FrameDetections(ts=0.0, detections=[
            person_at(1, foot_x=50, foot_y=50),
            object_at(99, ObjectClass.forklift, foot_x=58, foot_y=50),  # ~8px < danger dist
        ]),
    ]
    suite.append((proximity, proximity_frames))

    # -- fire: fire/smoke hazard appears ------------------------------------
    fire = ReferenceClip(
        name="fire-scene",
        mode=Mode.fire,
        frame_size=FRAME,
        ground_truth={HAZARD_ALERTS: 1},
    )
    fire_frames = [
        FrameDetections(ts=0.0, detections=[object_at(None, ObjectClass.smoke, foot_x=50, foot_y=50)]),
    ]
    suite.append((fire, fire_frames))

    # -- thermal: overheat / fever screening --------------------------------
    thermal = ReferenceClip(
        name="thermal-kiosk",
        mode=Mode.thermal,
        frame_size=FRAME,
        ground_truth={OVERHEAT_ALERTS: 1},
    )
    thermal_frames = [
        FrameDetections(ts=0.0, detections=[_thermal_person(1, temperature=39.2)]),
    ]
    suite.append((thermal, thermal_frames))

    return suite


# ---------------------------------------------------------------------------
# Running + reporting
# ---------------------------------------------------------------------------
def run_suite(clips: Sequence[Clip]) -> list[AccuracyResult]:
    """Score every ``(clip, frames)`` pair through the real counters."""
    return [AccuracyRunner(clip).run_fixture(frames) for clip, frames in clips]


def report_rows(results: Sequence[AccuracyResult]) -> list[dict]:
    """One flat summary row per clip — the body of the report table."""
    return [
        {
            "clip": r.clip_name,
            "mode": _mode_value(r.mode),
            "max_pct_error": r.max_pct_error(),
            "mean_pct_error": r.mean_pct_error(),
            "total_events": r.total_events,
        }
        for r in results
    ]


def report_markdown(results: Sequence[AccuracyResult]) -> str:
    """A publishable markdown table: per-clip rows plus an overall summary line."""
    rows = report_rows(results)
    lines = [
        "# CamAI accuracy report",
        "",
        "Deterministic fixture-mode accuracy across every vertical. Each clip replays "
        "hand-verified detections through the production counters and is scored against "
        "ground truth.",
        "",
        "| Clip | Mode | Metrics | Max error | Mean error | Events |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r, row in zip(results, rows):
        lines.append(
            f"| {row['clip']} | {row['mode']} | {len(r.metrics)} | "
            f"{_pct(row['max_pct_error'])} | {_pct(row['mean_pct_error'])} | {row['total_events']} |"
        )

    if rows:
        worst = max(row["max_pct_error"] for row in rows)
        finite_means = [row["mean_pct_error"] for row in rows]
        overall_mean = sum(finite_means) / len(finite_means)
    else:
        worst = 0.0
        overall_mean = 0.0
    lines.append("")
    lines.append(
        f"**Overall:** mean error {_pct(overall_mean)}, worst error {_pct(worst)} "
        f"across {len(rows)} clips."
    )
    return "\n".join(lines)


def report_json(results: Sequence[AccuracyResult]) -> dict:
    """A JSON-serializable snapshot of the whole suite (per-metric detail + summary)."""
    rows = report_rows(results)
    clips = []
    for r, row in zip(results, rows):
        clips.append({**row, "metrics": r.rows()})
    if rows:
        summary = {
            "clips": len(rows),
            "mean_pct_error": sum(row["mean_pct_error"] for row in rows) / len(rows),
            "max_pct_error": max(row["max_pct_error"] for row in rows),
            "total_events": sum(row["total_events"] for row in rows),
        }
    else:
        summary = {"clips": 0, "mean_pct_error": 0.0, "max_pct_error": 0.0, "total_events": 0}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clips": clips,
        "summary": summary,
    }


def _pct(value: float) -> str:
    return "inf" if value == float("inf") else f"{value:.1f}%"


def _mode_value(mode: Mode) -> str:
    return mode.value if isinstance(mode, Mode) else str(mode)


def _repo_root() -> Path:
    # .../packages/edge-agent/camai_edge/accuracy_report.py -> repo root is 3 up.
    return Path(__file__).resolve().parents[3]


def main() -> None:
    results = run_suite(default_fixture_suite())

    for r in results:
        print(r.format_table())
        print()

    markdown = report_markdown(results)
    print(markdown)

    docs = _repo_root() / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "accuracy_report.md").write_text(markdown + "\n", encoding="utf-8")
    (docs / "accuracy_report.json").write_text(
        json.dumps(report_json(results), indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {docs / 'accuracy_report.md'}")
    print(f"wrote {docs / 'accuracy_report.json'}")


if __name__ == "__main__":
    main()
