"""Scatter off-road neighbourhood scenery: broadleaf shade trees (in the yards along the streets) and low
chaparral scrub (on the open dry-grass hillsides).

Everything is placed by DISTANCE-TO-NEAREST-ROAD so nothing ever lands on the drivable surface: a point is
only eligible once it clears the road edge by a margin (palms already own the immediate parkway). Geometry
is opaque low-poly and DOUBLE-SIDED, so it always renders regardless of winding — no alpha-cutout to vanish.
Local frame (x=E, y=up, z=N); make_mesh remaps to Blender.
"""

from __future__ import annotations

import math
from collections import defaultdict


def _road_dist_fn(centerline, widths, bucket=60.0):
    """Return nearest(x,z) -> (distance_to_road_centreline, that_road's_half_width). Bucketed → O(1)."""
    rb = defaultdict(list)
    for i in range(len(centerline)):
        x, _y, z = centerline[i]
        rb[(int(x // bucket), int(z // bucket))].append((x, z, widths[i] / 2.0))
    def nearest(x, z):
        bx, bz = int(x // bucket), int(z // bucket)
        best_d, best_hw = 1e18, 8.0
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for (rx, rz, hw) in rb.get((bx + dx, bz + dz), ()):
                    d = (x - rx) ** 2 + (z - rz) ** 2
                    if d < best_d:
                        best_d, best_hw = d, hw
        return math.sqrt(best_d), best_hw
    return nearest


def _add_trunk(cx, cz, y0, h, r, mesh):
    V, T, U = mesh["vertices"], mesh["tris"], mesh["uvs"]
    SIDES = 6
    rings = []
    for k in range(3):
        t = k / 2.0
        yy = y0 + t * h
        rr = r * (1 - 0.3 * t)
        row = []
        for j in range(SIDES):
            a = 2 * math.pi * j / SIDES
            row.append(len(V))
            V.append((cx + rr * math.cos(a), yy, cz + rr * math.sin(a)))
            U.append((j / SIDES * 2.0, t * h / 1.2))
        rings.append(row)
    for k in range(2):
        for j in range(SIDES):
            j2 = (j + 1) % SIDES
            a, b, c, d = rings[k][j], rings[k][j2], rings[k + 1][j2], rings[k + 1][j]
            T.append((a, b, c)); T.append((a, c, d)); T.append((a, c, b)); T.append((a, d, c))


def _add_blob(cx, cy, cz, r, mesh, *, squash=1.0, seed=0, rings=4, seg=7):
    """A wobbly low-poly ellipsoid (canopy / bush), double-sided."""
    V, T, U = mesh["vertices"], mesh["tris"], mesh["uvs"]
    rows = []
    for i in range(rings + 1):
        theta = math.pi * i / rings
        y = cy + r * squash * math.cos(theta)
        rr = r * math.sin(theta)
        row = []
        for j in range(seg):
            phi = 2 * math.pi * j / seg
            wob = 1.0 + 0.14 * math.sin(phi * 3 + i * 1.3 + seed)
            row.append(len(V))
            V.append((cx + rr * wob * math.cos(phi), y, cz + rr * wob * math.sin(phi)))
            U.append((j / seg, i / rings))
        rows.append(row)
    for i in range(rings):
        for j in range(seg):
            j2 = (j + 1) % seg
            a, b, c, d = rows[i][j], rows[i][j2], rows[i + 1][j2], rows[i + 1][j]
            T.append((a, b, c)); T.append((a, c, d)); T.append((a, c, b)); T.append((a, d, c))


def scatter(grid_xyz, centerline, widths, *, tree_pct=40, scrub_pct=26,
            tree_band=(6.0, 22.0), scrub_band=(24.0, 95.0),
            tree_cap=450, scrub_cap=1000):
    """Walk the terrain grid; for each cell (jittered) place a shade tree in the near off-road ring or a
    scrub bush further out, gated by distance past the road edge. Returns
    ({'TREETRUNK':..,'TREECANOPY':..,'SCRUB':..}, n_trees, n_scrub)."""
    nearest = _road_dist_fn(centerline, widths)
    trunk = {"vertices": [], "tris": [], "uvs": []}
    canopy = {"vertices": [], "tris": [], "uvs": []}
    scrub = {"vertices": [], "tris": [], "uvs": []}
    ntree = nscrub = 0
    ny, nx = len(grid_xyz), len(grid_xyz[0])
    for r in range(ny):
        for c in range(nx):
            x, y, z = grid_xyz[r][c]
            hh = (r * 9277 + c * 2953) % 1000
            px = x + ((hh % 7) - 3) * 1.4
            pz = z + (((hh // 7) % 7) - 3) * 1.4
            off = nearest(px, pz)[0] - nearest(px, pz)[1]
            if off < tree_band[0]:
                continue                                   # on/too near the road → skip (palms own this)
            if off <= tree_band[1] and (hh % 100) < tree_pct and ntree < tree_cap:
                th = 5.5 + (hh % 5)                         # 5.5–9.5 m shade tree
                _add_trunk(px, pz, y, th * 0.42, 0.16 + 0.02 * (hh % 3), trunk)
                _add_blob(px, y + th * 0.62, pz, th * 0.38, canopy, squash=1.15, seed=hh, rings=4, seg=7)
                ntree += 1
            elif scrub_band[0] <= off <= scrub_band[1] and ((hh * 7) % 100) < scrub_pct and nscrub < scrub_cap:
                br = 0.7 + 0.5 * ((hh // 3) % 4) / 4.0      # 0.7–1.2 m dry bush
                _add_blob(px, y + br * 0.45, pz, br, scrub, squash=0.65, seed=hh * 3, rings=3, seg=6)
                nscrub += 1
    return {"TREETRUNK": trunk, "TREECANOPY": canopy, "SCRUB": scrub}, ntree, nscrub
