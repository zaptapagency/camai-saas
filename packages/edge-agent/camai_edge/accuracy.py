"""Accuracy regression harness for CamAI counting.

WHY this exists
---------------
The plan commits to "publish real accuracy numbers" and to "regression-test
accuracy, not just code". Unit tests in ``tests/test_counting.py`` prove the
counting *rules* are correct on hand-built inputs; this module proves the whole
counting *pipeline* hits a ground-truth number on a real (or replayed) clip and
reports the +/-% error a customer would actually see. That number is the sales
proof and the thing a regression must not silently move.

Two run modes, one metric surface
---------------------------------
* **detections-fixture mode** (:meth:`AccuracyRunner.run_fixture`) feeds a
  pre-recorded list of per-frame :class:`~camai_edge.detect.Detection` objects
  straight into the REAL counters. No video, no YOLO, no torch — so the accuracy
  *logic* (tally + error metrics) is deterministic and runs in CI under the plain
  test venv. This is what ``tests/test_accuracy.py`` uses.
* **video mode** (:meth:`AccuracyRunner.run_video`) plays an actual reference clip
  through the REAL :class:`~camai_edge.detect.Detector` (YOLO + ByteTrack) and the
  same counters, so the published numbers come from the same code path that runs
  on an edge box. The ML stack is imported lazily so importing this module (and
  running the fixture tests) never needs ultralytics/opencv installed.

Both modes converge on the same :class:`AccuracyResult`, so ground truth is
written once and compared identically whether the counts came from a fixture or
from real inference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from camai_schema import EventType, Mode

from camai_edge.config import CameraConfig, Line, Zone
from camai_edge.counting import make_counter
from camai_edge.detect import Detection

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing the counters early
    from camai_edge.config import Point


# ---------------------------------------------------------------------------
# Metric keys
# ---------------------------------------------------------------------------
# Ground truth and the computed tally share one flat namespace of metric keys so
# comparison is uniform regardless of mode. Global keys plus per-zone keys of the
# form ``zone:<zone_id>:<metric>`` let a single clip assert both totals and the
# breakdown that matters for parking/warehouse.
ENTRIES = "entries"
EXITS = "exits"
OCCUPANCY = "occupancy"          # retail running occupancy (entries - exits, clamped >= 0)
ARRIVALS = "arrivals"            # parking: total vehicle_parked events
DEPARTURES = "departures"        # parking: total vehicle_left events
NET_DELTA = "net_delta"          # warehouse: sum of signed count_delta values

# Alert/analytic verticals shipped after the first three modes. Each reduces its
# event type into a flat count (global, plus per-zone via zone_key when the event
# carries a zone). dwell additionally tracks a mean so "average wait" is scored,
# not just how many dwell events fired.
PPE_VIOLATIONS = "ppe_violations"        # safety: total ppe_violation events
VEHICLE_CROSSINGS = "vehicle_crossings"  # traffic: total vehicle_crossing events
DWELL_COUNT = "dwell_count"              # queue: number of dwell events (people who left)
MEAN_DWELL_SECONDS = "mean_dwell_seconds"  # queue: mean dwell_seconds over dwell events
CAPACITY_BREACHES = "capacity_breaches"  # capacity: total capacity_breach events
PROXIMITY_ALERTS = "proximity_alerts"    # proximity: total proximity_alert events
HAZARD_ALERTS = "hazard_alerts"          # fire: total hazard_alert events
OVERHEAT_ALERTS = "overheat_alerts"      # thermal: total overheat_alert events


def zone_key(zone_id: str, metric: str) -> str:
    """Namespaced per-zone metric key, e.g. ``zone:space-1:occupancy``."""
    return f"zone:{zone_id}:{metric}"


# ---------------------------------------------------------------------------
# Clip / fixture specs
# ---------------------------------------------------------------------------
@dataclass
class FrameDetections:
    """One replayed frame for detections-fixture mode.

    ``ts`` matters: the counters gate periodic ``occupancy_sample`` emission on
    elapsed time, so a fixture that wants to observe an occupancy sample must
    advance ``ts`` past the counter's sample interval. ``detections`` are in
    pixel coordinates (same as what the Detector emits), and the runner resolves
    the clip's normalized geometry against ``frame_size``.
    """

    ts: float
    detections: list[Detection] = field(default_factory=list)


@dataclass
class ReferenceClip:
    """A regression fixture: geometry + ground truth for one scene.

    A clip is mode-scoped (retail line-crossing, or parking/warehouse zones) and
    carries the *expected* counts a human established by watching the footage.
    ``path`` points at the video for :meth:`AccuracyRunner.run_video`; it is
    optional because fixture-mode clips have no video.
    """

    name: str
    mode: Mode
    frame_size: tuple[int, int]
    ground_truth: dict[str, float]
    lines: list[Line] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    path: Optional[str] = None
    # Video-mode sampling rate; ignored in fixture mode. Kept modest because the
    # counters are designed to work well below full frame rate.
    target_fps: float = 8.0

    def camera(self, camera_id: str = "acc-cam") -> CameraConfig:
        """Build the CameraConfig the real counters expect.

        We construct (not mutate) a CameraConfig here so the harness never has to
        touch the shared SiteConfig — the geometry a clip carries is exactly the
        per-camera geometry calibration would produce.
        """
        return CameraConfig(
            id=camera_id,
            source=self.path or "fixture",
            mode=self.mode,
            lines=list(self.lines),
            zones=list(self.zones),
            target_fps=self.target_fps,
        )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class MetricError:
    """Per-metric comparison of one measured count against ground truth."""

    metric: str
    expected: float
    actual: float

    @property
    def abs_error(self) -> float:
        return abs(self.actual - self.expected)

    @property
    def pct_error(self) -> float:
        """Percent error vs ground truth.

        When ground truth is 0 there is no meaningful percentage: an exact 0
        match is 0% error, any nonzero measurement is treated as fully wrong
        (``inf``) so it can never be hidden by a percentage tolerance — callers
        lean on ``abs_error`` for zero-valued expectations.
        """
        if self.expected == 0:
            return 0.0 if self.actual == 0 else float("inf")
        return 100.0 * self.abs_error / abs(self.expected)

    def as_row(self) -> dict[str, float | str]:
        return {
            "metric": self.metric,
            "expected": self.expected,
            "actual": self.actual,
            "abs_error": self.abs_error,
            "pct_error": self.pct_error,
        }


@dataclass
class AccuracyResult:
    """The outcome of running one clip: measured metrics vs ground truth."""

    clip_name: str
    mode: Mode
    metrics: dict[str, MetricError]
    total_events: int

    def max_pct_error(self) -> float:
        """Worst per-metric percent error — the number that headlines a clip."""
        if not self.metrics:
            return 0.0
        return max(m.pct_error for m in self.metrics.values())

    def mean_pct_error(self) -> float:
        finite = [m.pct_error for m in self.metrics.values() if m.pct_error != float("inf")]
        if not finite:
            return 0.0
        return sum(finite) / len(finite)

    def rows(self) -> list[dict[str, float | str]]:
        """Raw per-metric rows, sorted by metric name (stable for reports/CSV)."""
        return [self.metrics[k].as_row() for k in sorted(self.metrics)]

    def failures(self, tolerance_pct: float, abs_tolerance: float = 0.0) -> list[MetricError]:
        """Metrics that miss BOTH the percent and absolute tolerance.

        A metric passes if it is within the percentage band OR within the
        absolute band; small integer counts (where one miscount is a huge
        percentage) are rescued by ``abs_tolerance``.
        """
        out: list[MetricError] = []
        for m in self.metrics.values():
            if m.abs_error <= abs_tolerance:
                continue
            if m.pct_error <= tolerance_pct:
                continue
            out.append(m)
        return out

    def assert_within(self, tolerance_pct: float, *, abs_tolerance: float = 0.0) -> "AccuracyResult":
        """Raise ``AssertionError`` unless every metric is within tolerance.

        Returns ``self`` on success so it chains in a test. The message is a
        readable table so a regression failure shows exactly which metric drifted
        and by how much — that is the whole point of the harness.
        """
        bad = self.failures(tolerance_pct, abs_tolerance)
        if not bad:
            return self
        lines = [
            f"clip {self.clip_name!r} exceeded tolerance "
            f"(pct<={tolerance_pct}% or abs<={abs_tolerance}):"
        ]
        for m in sorted(bad, key=lambda e: e.metric):
            lines.append(
                f"  {m.metric}: expected {m.expected:g}, got {m.actual:g} "
                f"(abs {m.abs_error:g}, {m.pct_error:.1f}%)"
            )
        raise AssertionError("\n".join(lines))

    def format_table(self) -> str:
        """A compact human-readable table, e.g. for a CLI run or docs paste."""
        header = f"{'metric':<28}{'expected':>10}{'actual':>10}{'abs':>8}{'pct':>9}"
        out = [f"clip: {self.clip_name}  (mode={_mode_value(self.mode)})", header, "-" * len(header)]
        for k in sorted(self.metrics):
            m = self.metrics[k]
            pct = "inf" if m.pct_error == float("inf") else f"{m.pct_error:.1f}%"
            out.append(f"{m.metric:<28}{m.expected:>10g}{m.actual:>10g}{m.abs_error:>8g}{pct:>9}")
        out.append(f"max error: {self.max_pct_error():.1f}%   events emitted: {self.total_events}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class AccuracyRunner:
    """Plays a clip through the real counter and scores it against ground truth."""

    def __init__(self, clip: ReferenceClip, *, tenant_id: str = "acc", site_id: str = "acc") -> None:
        self.clip = clip
        self.tenant_id = tenant_id
        self.site_id = site_id

    # -- public entry points -------------------------------------------------
    def run_fixture(self, frames: Sequence[FrameDetections]) -> AccuracyResult:
        """Score a clip from a pre-recorded list of per-frame detections.

        No video and no YOLO: this is the deterministic path that lets the
        accuracy *logic* be regression-tested in CI. Detections are fed to the
        exact same counter the edge agent runs in production.
        """
        camera = self.clip.camera()
        counter = make_counter(self.tenant_id, self.site_id, camera)
        emitted = []
        for frame in frames:
            emitted.extend(counter.update(frame.detections, self.clip.frame_size, frame.ts))
        return self._score(emitted)

    def run_video(self, *, weights: str = "yolo11n.pt", device: str = "auto", imgsz: int = 640):
        """Score a clip by running real detection + tracking over its video.

        The ML stack is imported lazily HERE so importing this module stays cheap
        and the fixture tests never need torch/ultralytics/opencv. Timestamps are
        derived from the video's own frame position (not wall clock) so the run
        is reproducible regardless of how fast the machine decodes.
        """
        if not self.clip.path:
            raise ValueError(f"clip {self.clip.name!r} has no video path for run_video()")

        from camai_edge.detect import Detector  # lazy: pulls in ultralytics/torch

        detector = Detector(
            weights=weights,
            device=device,
            imgsz=imgsz,
            min_confidence=self.clip.camera().min_confidence,
        )
        camera = self.clip.camera()
        counter = make_counter(self.tenant_id, self.site_id, camera)

        emitted = []
        for image, ts, frame_size in self._decode_video():
            detections = detector.track(image)
            emitted.extend(counter.update(detections, frame_size, ts))
        return self._score(emitted)

    # -- internals -----------------------------------------------------------
    def _decode_video(self) -> Iterable[tuple[object, float, tuple[int, int]]]:
        """Yield (image, derived_ts, (w, h)), sampled to the clip's target_fps.

        We derive ``ts`` from the source frame index and the video's native FPS
        so counting is deterministic across machines — the harness must produce
        the same numbers every run to be a regression test.
        """
        import cv2  # lazy: opencv is an ML-stack dependency

        cap = cv2.VideoCapture(self.clip.path)
        if not cap.isOpened():
            raise RuntimeError(f"could not open reference clip: {self.clip.path!r}")
        try:
            native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            stride = max(1, int(round(native_fps / self.clip.target_fps)))
            src_index = 0
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                if src_index % stride == 0:
                    ts = src_index / native_fps
                    h, w = image.shape[:2]
                    yield image, ts, (w, h)
                src_index += 1
        finally:
            cap.release()

    def _score(self, events) -> AccuracyResult:
        actual = tally_metrics(events, self.clip.mode)
        metrics = {
            key: MetricError(metric=key, expected=float(expected), actual=float(actual.get(key, 0.0)))
            for key, expected in self.clip.ground_truth.items()
        }
        return AccuracyResult(
            clip_name=self.clip.name,
            mode=self.clip.mode,
            metrics=metrics,
            total_events=len(events),
        )


# ---------------------------------------------------------------------------
# Tally
# ---------------------------------------------------------------------------
def tally_metrics(events: Iterable[object], mode: Mode) -> dict[str, float]:
    """Reduce a stream of emitted events into the flat metric namespace.

    This is the bridge between "what the counter emitted" and "what ground truth
    is written against". It intentionally computes a superset of metrics; the
    scorer only compares the keys a clip's ground truth actually specifies.
    """
    m: dict[str, float] = {}

    def bump(key: str, by: float = 1.0) -> None:
        m[key] = m.get(key, 0.0) + by

    # last instantaneous occupancy_sample seen, globally and per zone
    last_zone_occ: dict[str, float] = {}

    # Running sums for dwell so we can emit a mean at the end (global + per zone).
    dwell_sum = 0.0
    dwell_n = 0
    zone_dwell_sum: dict[str, float] = {}
    zone_dwell_n: dict[str, int] = {}

    for e in events:
        etype = e.type  # str under use_enum_values, compares equal to EventType members
        zid = e.zone_id

        if etype == EventType.entry:
            bump(ENTRIES)
        elif etype == EventType.exit:
            bump(EXITS)
        elif etype == EventType.vehicle_parked:
            bump(ARRIVALS)
            if zid is not None:
                bump(zone_key(zid, ARRIVALS))
        elif etype == EventType.vehicle_left:
            bump(DEPARTURES)
            if zid is not None:
                bump(zone_key(zid, DEPARTURES))
        elif etype == EventType.count_delta:
            d = float(e.delta or 0)
            bump(NET_DELTA, d)
            if zid is not None:
                bump(zone_key(zid, NET_DELTA), d)
        elif etype == EventType.ppe_violation:
            bump(PPE_VIOLATIONS)
            if zid is not None:
                bump(zone_key(zid, PPE_VIOLATIONS))
        elif etype == EventType.vehicle_crossing:
            bump(VEHICLE_CROSSINGS)
            if zid is not None:
                bump(zone_key(zid, VEHICLE_CROSSINGS))
        elif etype == EventType.capacity_breach:
            bump(CAPACITY_BREACHES)
            if zid is not None:
                bump(zone_key(zid, CAPACITY_BREACHES))
        elif etype == EventType.proximity_alert:
            bump(PROXIMITY_ALERTS)
            if zid is not None:
                bump(zone_key(zid, PROXIMITY_ALERTS))
        elif etype == EventType.hazard_alert:
            bump(HAZARD_ALERTS)
            if zid is not None:
                bump(zone_key(zid, HAZARD_ALERTS))
        elif etype == EventType.overheat_alert:
            bump(OVERHEAT_ALERTS)
            if zid is not None:
                bump(zone_key(zid, OVERHEAT_ALERTS))
        elif etype == EventType.dwell:
            bump(DWELL_COUNT)
            secs = float(e.dwell_seconds or 0.0)
            dwell_sum += secs
            dwell_n += 1
            if zid is not None:
                bump(zone_key(zid, DWELL_COUNT))
                zone_dwell_sum[zid] = zone_dwell_sum.get(zid, 0.0) + secs
                zone_dwell_n[zid] = zone_dwell_n.get(zid, 0) + 1
        elif etype == EventType.occupancy_sample:
            count = float(e.count or 0)
            if zid is None:
                m[f"{OCCUPANCY}_sample"] = count  # latest retail sample, if emitted
            else:
                last_zone_occ[zid] = count

    # Retail running occupancy: derive from entries - exits (clamped, matching the
    # counter's internal accounting) so it is available even when no periodic
    # occupancy_sample happened to be emitted within the clip's timespan.
    if mode == Mode.retail:
        m[OCCUPANCY] = max(0.0, m.get(ENTRIES, 0.0) - m.get(EXITS, 0.0))

    for zid, count in last_zone_occ.items():
        m[zone_key(zid, OCCUPANCY)] = count

    # Mean dwell (average wait) — only meaningful when at least one dwell fired.
    if dwell_n:
        m[MEAN_DWELL_SECONDS] = dwell_sum / dwell_n
    for zid, n in zone_dwell_n.items():
        if n:
            m[zone_key(zid, MEAN_DWELL_SECONDS)] = zone_dwell_sum[zid] / n

    return m


def _mode_value(mode: Mode) -> str:
    return mode.value if isinstance(mode, Mode) else str(mode)


# ---------------------------------------------------------------------------
# Fixture-authoring helpers
# ---------------------------------------------------------------------------
# These build pixel-space Detection objects the way the real Detector would, so
# fixtures read declaratively ("a person whose foot point is here") instead of
# repeating bounding-box arithmetic.
def person_at(track_id: int, foot_x: float, foot_y: float, *, half_w: float = 5.0, height: float = 20.0) -> Detection:
    from camai_schema import ObjectClass

    return Detection(
        track_id=track_id, object_class=ObjectClass.person, confidence=0.9,
        x1=foot_x - half_w, y1=foot_y - height, x2=foot_x + half_w, y2=foot_y,
    )


def vehicle_at(track_id: int, foot_x: float, foot_y: float, *, half_w: float = 10.0, height: float = 20.0) -> Detection:
    from camai_schema import ObjectClass

    return Detection(
        track_id=track_id, object_class=ObjectClass.vehicle, confidence=0.9,
        x1=foot_x - half_w, y1=foot_y - height, x2=foot_x + half_w, y2=foot_y,
    )


def object_at(track_id: int, object_class, foot_x: float, foot_y: float, *, half_w: float = 8.0, height: float = 16.0) -> Detection:
    """Generic detection builder for warehouse classes (forklift/pallet/person)."""
    return Detection(
        track_id=track_id, object_class=object_class, confidence=0.9,
        x1=foot_x - half_w, y1=foot_y - height, x2=foot_x + half_w, y2=foot_y,
    )


__all__ = [
    "ReferenceClip",
    "FrameDetections",
    "AccuracyRunner",
    "AccuracyResult",
    "MetricError",
    "tally_metrics",
    "zone_key",
    "person_at",
    "vehicle_at",
    "object_at",
    "ENTRIES",
    "EXITS",
    "OCCUPANCY",
    "ARRIVALS",
    "DEPARTURES",
    "NET_DELTA",
    "PPE_VIOLATIONS",
    "VEHICLE_CROSSINGS",
    "DWELL_COUNT",
    "MEAN_DWELL_SECONDS",
    "CAPACITY_BREACHES",
    "PROXIMITY_ALERTS",
    "HAZARD_ALERTS",
    "OVERHEAT_ALERTS",
]
