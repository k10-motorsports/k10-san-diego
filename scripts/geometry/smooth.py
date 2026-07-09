"""Gentle horizontal corner-rounding for a centreline — preserves elevation, point count and closure.

An OSM polyline turns a real curve into a handful of straight segments meeting at sharp vertices, so a
sweep down it shows "straight angles" at every corner. A count-preserving Gaussian moving average over
the horizontal (X,Z) plane rounds those kinks into smooth arcs. Elevation (Y) is left untouched per
index — the horizontal moves are sub-metre, so the road stays tight to the real 3DEP profile (the whole
point of this project). Straight runs are unaffected (averaging colinear points is a no-op), so ONLY the
corners round; the loop doesn't shrink perceptibly.

Pure (no bpy) so it's unit-testable and shared by the builder + any resample step.
"""

from __future__ import annotations

import math

Vertex = tuple[float, float, float]


def _gauss_kernel(radius: int, sigma: float) -> list[float]:
    w = [math.exp(-(k * k) / (2.0 * sigma * sigma)) for k in range(-radius, radius + 1)]
    s = sum(w) or 1.0
    return [x / s for x in w]


def _smooth_channel(vals: list[float], radius: int, ker: list[float], passes: int, closed: bool) -> list[float]:
    m = len(vals)
    for _ in range(int(passes)):
        nv = [0.0] * m
        for i in range(m):
            acc = 0.0
            for k in range(-radius, radius + 1):
                j = (i + k) % m if closed else min(m - 1, max(0, i + k))
                acc += ker[k + radius] * vals[j]
            nv[i] = acc
        if not closed:
            nv[0], nv[-1] = vals[0], vals[-1]          # pin open endpoints
        vals = nv
    return vals


def smooth_centerline(points: list[Vertex], *, sigma_pts: float = 2.0, passes: int = 2,
                      sigma_y_pts: float = 0.0, y_passes: int = 2, radius: int | None = None) -> list[Vertex]:
    """Round the kinks in ``points`` [(x,y,z)...]. X,Z are Gaussian-smoothed horizontally (wrap-around for a
    closed loop, endpoints pinned for an open line). ``sigma_y_pts`` (>0) ALSO smooths the vertical Y profile
    along the arc — this removes the bare-earth DEM's steps/notches (bridges sag to the freeway floor, etc.)
    that launch the car, while keeping the real overall grade. Returns a NEW list, same length + closure.
    A no-op when ``sigma_pts<=0`` or ``passes<=0`` (horizontal); Y is preserved when ``sigma_y_pts<=0``."""
    n = len(points)
    horiz = sigma_pts > 0 and passes > 0
    vert = sigma_y_pts > 0 and y_passes > 0
    if n < 5 or not (horiz or vert):
        return [tuple(p) for p in points]
    closed = abs(points[0][0] - points[-1][0]) < 1e-6 and abs(points[0][2] - points[-1][2]) < 1e-6
    ring = points[:-1] if closed else points          # unique vertices (drop the closing duplicate)
    m = len(ring)
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    zs = [p[2] for p in ring]
    if horiz:
        r = radius if radius else max(1, int(round(3.0 * sigma_pts)))
        ker = _gauss_kernel(r, sigma_pts)
        xs = _smooth_channel(xs, r, ker, passes, closed)
        zs = _smooth_channel(zs, r, ker, passes, closed)
    if vert:
        ry = max(1, int(round(3.0 * sigma_y_pts)))
        kery = _gauss_kernel(ry, sigma_y_pts)
        ys = _smooth_channel(ys, ry, kery, y_passes, closed)
    out: list[Vertex] = [(xs[i], ys[i], zs[i]) for i in range(m)]
    if closed:
        out.append(out[0])                             # restore the closing duplicate vertex
    return out


def max_kink_deg(points: list[Vertex]) -> float:
    """Largest heading change between consecutive segments, in degrees (a facet-sharpness metric)."""
    worst = 0.0
    for i in range(1, len(points) - 1):
        a = math.atan2(points[i][2] - points[i - 1][2], points[i][0] - points[i - 1][0])
        b = math.atan2(points[i + 1][2] - points[i][2], points[i + 1][0] - points[i][0])
        d = abs((b - a + math.pi) % (2 * math.pi) - math.pi)
        worst = max(worst, d)
    return math.degrees(worst)
