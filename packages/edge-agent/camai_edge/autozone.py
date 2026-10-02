"""Auto-calibration assist: propose zones/lines from observed detections.

Zero-touch onboarding means an installer should *review and confirm* calibration
geometry, not draw it from scratch on every one of 250k cameras. This module turns
a pile of accumulated :class:`~camai_edge.detect.Detection` objects (gathered over
many frames while the camera simply watches its scene) into suggested
:class:`~camai_edge.config.Zone` / :class:`~camai_edge.config.Line` geometry.

It is a deliberately small, deterministic, pure-stdlib heuristic:

* Zones come from a coarse ``grid x grid`` histogram of detection foot points.
  The densest connected clump(s) of cells become rectangular ROIs — a parking row,
  a checkout area, a workstation cluster — covering where activity actually happens.
* A line comes from the dominant travel axis: if people spread more vertically than
  horizontally, a horizontal counting line sits at the median y (and vice-versa).

Everything is normalized to [0, 1] (resolution-independent, like the rest of config)
and returned as real pydantic ``Zone`` / ``Line`` objects, so the output drops
straight into a ``CameraConfig`` for the installer to accept or nudge. No numpy,
opencv or torch — this stays trivially unit-testable and runs on any edge box.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

from camai_schema import Mode

from camai_edge.config import Line, Point, Zone
from camai_edge.detect import Detection

# Modes whose counting is line-based (directional crossings) rather than ROI-based.
_LINE_MODES = {Mode.retail, Mode.traffic}

# Below this many detections a line estimate is noise, not a travel axis.
_MIN_LINE_DETECTIONS = 5


def accumulate(detections: Iterable) -> list[Detection]:
    """Flatten accumulated detections into one flat list.

    Accepts either a flat iterable of :class:`Detection` or an iterable of
    per-frame detection lists (what you get by stashing ``detector.track(frame)``
    each frame). A bare ``Detection`` is treated as atomic; anything else iterable
    is flattened one level. Handy so callers can feed frames straight in.
    """
    out: list[Detection] = []
    for item in detections:
        if isinstance(item, Detection):
            out.append(item)
        elif isinstance(item, Iterable):
            out.extend(d for d in item if isinstance(d, Detection))
    return out


def _clamp01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _norm_foot_points(
    detections: Iterable[Detection], frame_size: tuple[int, int]
) -> list[tuple[float, float]]:
    """Normalized (x, y) foot points in [0, 1], one per detection."""
    width, height = frame_size
    width = width or 1
    height = height or 1
    pts: list[tuple[float, float]] = []
    for d in detections:
        fx, fy = d.foot_point
        pts.append((_clamp01(fx / width), _clamp01(fy / height)))
    return pts


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _variance(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / n


def suggest_zones(
    detections: Iterable[Detection],
    frame_size: tuple[int, int],
    *,
    max_zones: int = 3,
    grid: int = 16,
    min_share: float = 0.08,
) -> list[Zone]:
    """Propose up to ``max_zones`` rectangular ROIs over the densest activity.

    Builds a ``grid x grid`` histogram of normalized foot points, marks the hottest
    cells, merges adjacent hot cells (8-connectivity) into rectangular clusters, and
    emits a normalized 4-point (clockwise) ``Zone`` per cluster. A cluster is kept
    only if its share of all detections is ``>= min_share``. Zones are ordered
    densest-first and get stable ids ``zone-1``, ``zone-2``, ... Deterministic.
    """
    pts = _norm_foot_points(detections, frame_size)
    total = len(pts)
    if total == 0 or grid < 1 or max_zones < 1:
        return []

    # 1) Histogram: cell (cx, cy) -> count.
    counts: dict[tuple[int, int], int] = {}
    for x, y in pts:
        cx = min(grid - 1, int(x * grid))
        cy = min(grid - 1, int(y * grid))
        counts[(cx, cy)] = counts.get((cx, cy), 0) + 1

    peak = max(counts.values())
    # A cell is "hot" if it holds a meaningful fraction of the busiest cell. This
    # scales with the scene and keeps sparse background noise out of clusters.
    hot_threshold = max(1.0, peak * 0.3)
    hot = {cell for cell, n in counts.items() if n >= hot_threshold}
    if not hot:
        return []

    # 2) Merge adjacent hot cells into connected clusters (8-connectivity).
    clusters: list[set[tuple[int, int]]] = []
    seen: set[tuple[int, int]] = set()
    neighbours = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for cell in sorted(hot):
        if cell in seen:
            continue
        comp: set[tuple[int, int]] = set()
        queue = deque([cell])
        seen.add(cell)
        while queue:
            ccx, ccy = queue.popleft()
            comp.add((ccx, ccy))
            for dx, dy in neighbours:
                nb = (ccx + dx, ccy + dy)
                if nb in hot and nb not in seen:
                    seen.add(nb)
                    queue.append(nb)
        clusters.append(comp)

    # 3) Score clusters by contained detections; keep those meeting min_share.
    scored: list[tuple[int, set[tuple[int, int]]]] = []
    for comp in clusters:
        pop = sum(counts[c] for c in comp)
        if pop / total >= min_share:
            scored.append((pop, comp))

    # Densest first; tie-break on position for determinism.
    scored.sort(key=lambda t: (-t[0], min(t[1])))

    zones: list[Zone] = []
    for idx, (_pop, comp) in enumerate(scored[:max_zones], start=1):
        cxs = [c[0] for c in comp]
        cys = [c[1] for c in comp]
        x0 = _clamp01(min(cxs) / grid)
        x1 = _clamp01((max(cxs) + 1) / grid)
        y0 = _clamp01(min(cys) / grid)
        y1 = _clamp01((max(cys) + 1) / grid)
        # Clockwise (screen coords, y down): TL -> TR -> BR -> BL.
        polygon = [
            Point(x=x0, y=y0),
            Point(x=x1, y=y0),
            Point(x=x1, y=y1),
            Point(x=x0, y=y1),
        ]
        zones.append(Zone(id=f"zone-{idx}", polygon=polygon))
    return zones


def suggest_line(
    detections: Iterable[Detection], frame_size: tuple[int, int]
) -> Line | None:
    """Propose ONE counting line across the busiest travel corridor.

    Estimates the dominant travel axis from the spread (variance) of foot points:
    if vertical spread dominates, people move up/down, so a horizontal line at the
    median y catches them; otherwise a vertical line at the median x. Returns
    ``None`` when there are too few detections to trust. Deterministic.
    """
    pts = _norm_foot_points(detections, frame_size)
    if len(pts) < _MIN_LINE_DETECTIONS:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    var_x = _variance(xs)
    var_y = _variance(ys)

    if var_y >= var_x:
        # Vertical movement dominates -> horizontal line spanning the frame.
        my = _clamp01(_median(ys))
        a = Point(x=0.0, y=my)
        b = Point(x=1.0, y=my)
    else:
        # Horizontal movement dominates -> vertical line spanning the frame.
        mx = _clamp01(_median(xs))
        a = Point(x=mx, y=0.0)
        b = Point(x=mx, y=1.0)
    return Line(id="line-1", a=a, b=b)


def suggest_geometry(
    detections: Iterable[Detection], frame_size: tuple[int, int], mode: Mode
) -> dict:
    """Propose the geometry that fits ``mode``: lines for flow, zones for ROIs.

    * Line-based modes (retail, traffic) get a single crossing line.
    * Everything else (parking, warehouse, staffing, capacity, queue, safety,
      thermal, proximity, fire) gets rectangular zones.

    Returns ``{"zones": [...], "lines": [...]}`` of real ``Zone`` / ``Line``
    objects. One kind will typically be empty. Deterministic.
    """
    mode_val = mode.value if isinstance(mode, Mode) else str(mode)
    zones: list[Zone] = []
    lines: list[Line] = []

    if mode_val in {m.value for m in _LINE_MODES}:
        line = suggest_line(detections, frame_size)
        if line is not None:
            lines.append(line)
    else:
        zones = suggest_zones(detections, frame_size)

    # Dedupe zones with identical polygons (a belt-and-braces guard; the histogram
    # already yields disjoint clusters). Keep first occurrence / its id.
    deduped: list[Zone] = []
    seen_polys: set[tuple] = set()
    for z in zones:
        key = tuple((round(p.x, 6), round(p.y, 6)) for p in z.polygon)
        if key in seen_polys:
            continue
        seen_polys.add(key)
        deduped.append(z)

    return {"zones": deduped, "lines": lines}
