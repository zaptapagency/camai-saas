"""Pure-Python geometry helpers for counting.

Zones/lines are stored normalized ([0,1]) so they're resolution-independent; these
helpers resolve them against a frame's pixel size and answer the two questions the
counters need: "which side of this line is a point on?" and "is this point inside
this polygon?". No numpy/opencv dependency so this stays trivially unit-testable.
"""

from __future__ import annotations

from camai_edge.config import Line, Point, Zone


def to_pixels(p: Point, width: int, height: int) -> tuple[float, float]:
    return p.x * width, p.y * height


def line_side(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Signed cross product of a->b and a->p.

    > 0 : point is on the left of a->b
    < 0 : point is on the right
    = 0 : point is on the line
    """
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def point_in_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        intersects = ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def resolve_line(line: Line, width: int, height: int) -> tuple[float, float, float, float]:
    ax, ay = to_pixels(line.a, width, height)
    bx, by = to_pixels(line.b, width, height)
    return ax, ay, bx, by


def resolve_zone(zone: Zone, width: int, height: int) -> list[tuple[float, float]]:
    return [to_pixels(p, width, height) for p in zone.polygon]
