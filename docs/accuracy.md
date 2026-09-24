# Accuracy harness — regression-testing counting, and publishing real numbers

CamAI's value is a **number a customer can trust** ("we counted 1,240 visitors,
±3%"). This harness turns that promise into something we test on every change and
can put on a sales slide. It lives in
`packages/edge-agent/camai_edge/accuracy.py`.

It answers two different questions with the same code:

1. **Does the counting *logic* still work?** — runs in CI, no ML stack, fully
   deterministic (detections-fixture mode).
2. **What accuracy do we actually deliver on real footage?** — runs the real
   YOLO + ByteTrack detector over reference clips and reports the ±% error vs.
   human-verified ground truth (video mode).

## Concepts

- **`ReferenceClip`** — a scene spec: `mode`, `frame_size`, the `lines`/`zones`
  geometry (normalized `[0,1]`, exactly what calibration produces), a
  `ground_truth` dict of expected counts, and (for video mode) a `path`.
- **Ground truth** is a flat dict of metric → expected value. Keys:
  `entries`, `exits`, `occupancy` (retail); `arrivals`, `departures` (parking);
  `net_delta` (warehouse); plus per-zone keys `zone:<zone_id>:<metric>`
  (build them with `zone_key("space-1", "occupancy")`).
- **`AccuracyRunner`** plays the clip through the **real** counters
  (`make_counter` → the same `LineCrossingCounter` / `ZoneOccupancyCounter` the
  edge agent runs) and returns an **`AccuracyResult`**.
- **`AccuracyResult`** holds a `MetricError` per metric (`abs_error`,
  `pct_error`), plus `max_pct_error()`, `format_table()`, and
  `assert_within(tolerance_pct, abs_tolerance=...)` — a metric passes if it is
  within the percent band **or** the absolute band (so one miscount on a tiny
  count isn't a spurious 100% failure).

## Running it

### CI / deterministic logic tests (no torch, no video)

```
pytest packages/edge-agent/tests/test_accuracy.py
```

These use `run_fixture(...)`: you hand-craft a list of `FrameDetections` (each a
`ts` plus pixel-space `Detection` objects, built with the `person_at` /
`vehicle_at` / `object_at` helpers), and assert the tallied counts and %-error.
This is where a regression in the counting rules gets caught, and it needs only
`pydantic` + `pytest`.

### Real accuracy over a reference clip (needs the ML venv)

```python
from camai_schema import Mode
from camai_edge.config import Line, Point
from camai_edge.accuracy import ReferenceClip, AccuracyRunner, ENTRIES, EXITS

clip = ReferenceClip(
    name="store-42-front-door-morning",
    mode=Mode.retail,
    frame_size=(1920, 1080),          # informational; video mode reads real size
    path="reference_clips/store-42-front-door-morning.mp4",
    lines=[Line(id="door", a=Point(x=0.1, y=0.55), b=Point(x=0.9, y=0.55))],
    ground_truth={ENTRIES: 214, EXITS: 198},   # counted by hand from the footage
)

result = AccuracyRunner(clip).run_video(weights="yolo11n.pt")  # lazy-imports YOLO
print(result.format_table())
result.assert_within(5.0)             # our published tolerance
```

`run_video` derives timestamps from the video's own frame index (not wall clock),
so the numbers are reproducible run to run and machine to machine.

## Adding a real reference clip

1. Capture (or get customer consent to keep) a representative clip per mode:
   retail door, parking row, warehouse bay. Vary lighting/traffic — publish the
   worst case, not the best.
2. Store the video under `packages/edge-agent/reference_clips/` (git-ignored;
   keep the actual media in object storage / DVC — do **not** commit customer
   video). Commit only the `ReferenceClip` spec + ground truth.
3. Establish ground truth by **watching the clip and counting by hand** (two
   people, reconcile). That human number is the source of truth the ±% is
   measured against.
4. Add the clip to a suite that runs `run_video(...).assert_within(tolerance)`
   nightly (not in the fast PR CI — it needs the GPU/ML venv).

## Publishing the numbers as sales proof

- Run each clip, collect `result.max_pct_error()` / `result.rows()`, and record
  them per release. `format_table()` gives a paste-ready summary.
- Headline the **worst** clip's error, per mode, with clip conditions
  ("crowded, backlit"). An honest ±% beats an unqualified accuracy claim.
- Because the harness runs the exact production counters, the published number is
  the number the box delivers — that is the point.

See also `docs/PLAN.md` ("publish real accuracy numbers", "regression-test
accuracy, not just code").
