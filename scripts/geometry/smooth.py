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


def smooth_centerline(points: list[Vertex], *, sigma_pts: float = 2.0, passes: int = 2,
                      radius: int | None = None) -> list[Vertex]:
    """Round the horizontal kinks in ``points`` [(x,y,z)...]. X,Z are Gaussian-smoothed (wrap-around for a
    closed loop, endpoints pinned for an open line); Y is preserved per index. Returns a NEW list with the
    same length and same closure. ``sigma_pts``/``passes`` control roundness; larger = smoother (but cuts
    corners more). A no-op when ``sigma_pts<=0`` or ``passes<=0``."""
    n = len(points)
    if n < 5 or sigma_pts <= 0 or passes <= 0:
        return [tuple(p) for p in points]
    closed = abs(points[0][0] - points[-1][0]) < 1e-6 and abs(points[0][2] - points[-1][2]) < 1e-6
    ring = points[:-1] if closed else points          # unique vertices (drop the closing duplicate)
    m = len(ring)
    radius = radius if radius else max(1, int(round(3.0 * sigma_pts)))
    ker = _gauss_kernel(radius, sigma_pts)
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    zs = [p[2] for p in ring]
    for _ in range(int(passes)):
        nx = [0.0] * m
        nz = [0.0] * m
        for i in range(m):
            ax = az = 0.0
            for k in range(-radius, radius + 1):
                w = ker[k + radius]
                j = (i + k) % m if closed else min(m - 1, max(0, i + k))
                ax += w * xs[j]
                az += w * zs[j]
            nx[i] = ax
            nz[i] = az
        if not closed:
            nx[0], nz[0] = xs[0], zs[0]                # pin the open endpoints so the route still starts/ends put
            nx[-1], nz[-1] = xs[-1], zs[-1]
        xs, zs = nx, nz
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
