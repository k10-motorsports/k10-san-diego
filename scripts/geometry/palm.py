"""Scatter tall Mexican fan palms along a road's verges — trunk (bark) + drooping fan crown (frond cards).

Geometry is authored in the LOCAL frame (x=E, y=up, z=N), the same frame build_loop_blend feeds make_mesh.
Both the trunk cylinder and the frond cards are DOUBLE-SIDED, so the coordinate-reflection in make_mesh
can't turn them inside-out and the thin frond cards read from every angle. Returns two meshes so they can
carry different materials: PALMTRUNK (opaque bark) and PALMFROND (alpha-cutout leaves).
"""

from __future__ import annotations

import math

# one frond centreline: (t along frond, up factor, width scale) — rises then droops past the tip
_PROF = [(0.00, 0.00, 0.30), (0.20, 0.17, 0.85), (0.45, 0.24, 1.00),
         (0.70, 0.18, 0.82), (0.88, 0.03, 0.5), (1.00, -0.16, 0.16)]
# crown tiers: (count, length, up-pitch, droop, yaw offset) — upright top ring → long drooping skirt
_TIERS = [(6, 3.0, 1.35, 1.0, 0.0), (8, 3.9, 0.85, 2.1, 0.28), (9, 4.4, 0.35, 3.3, 0.6)]
_REF_H = 11.5                    # height the profile constants are tuned for; everything scales off it


def _add_trunk(cx, cz, y0, h, mesh):
    V, T, U = mesh["vertices"], mesh["tris"], mesh["uvs"]
    SIDES, RINGS = 10, 6
    s = h / _REF_H
    rb, rt = 0.34 * s, 0.21 * s
    rings = []
    for k in range(RINGS + 1):
        t = k / RINGS
        r = (rb * (1 - t) + rt * t) * (1.0 + 0.14 * math.exp(-t * 6))
        yy = y0 + t * h
        row = []
        for j in range(SIDES):
            a = 2 * math.pi * j / SIDES
            row.append(len(V))
            V.append((cx + r * math.cos(a), yy, cz + r * math.sin(a)))
            U.append((j / SIDES * 2.0, t * h / 1.5))
        rings.append(row)
    for k in range(RINGS):
        for j in range(SIDES):
            j2 = (j + 1) % SIDES
            a, b, c, d = rings[k][j], rings[k][j2], rings[k + 1][j2], rings[k + 1][j]
            T.append((a, b, c)); T.append((a, c, d))       # outer
            T.append((a, c, b)); T.append((a, d, c))       # inner (double-sided)


def _add_fronds(cx, cz, ytop, h, yaw0, mesh):
    V, T, U = mesh["vertices"], mesh["tris"], mesh["uvs"]
    s = h / _REF_H
    W = 1.15 * s
    for count, L, pitch, droop, y0 in _TIERS:
        L *= s; dr = droop * s
        for i in range(count):
            yaw = yaw0 + y0 + 2 * math.pi * i / count + 0.15 * math.sin(i * 2.3)
            rx, rz = math.cos(yaw), math.sin(yaw)          # radial (outward) in the x-z plane
            tx, tz = -math.sin(yaw), math.cos(yaw)         # width dir (horizontal, tangential)
            ring = []
            for (tt, upv, wsc) in _PROF:
                ox = L * tt
                uy = L * upv * pitch - dr * tt * tt        # up (y) — arch then droop
                px, py, pz = cx + rx * ox, ytop + uy, cz + rz * ox
                half = W * wsc / 2
                ai = len(V); V.append((px - tx * half, py, pz - tz * half)); U.append((0.0, tt))
                bi = len(V); V.append((px + tx * half, py, pz + tz * half)); U.append((1.0, tt))
                ring.append((ai, bi))
            for j in range(len(ring) - 1):
                a0, b0 = ring[j]; a1, b1 = ring[j + 1]
                T.append((a0, b0, b1)); T.append((a0, b1, a1))     # front
                T.append((a0, b1, b0)); T.append((a0, a1, b1))     # back (double-sided)


def scatter(centerline, widths, terr, on_mask, *, spacing=28.0, offset=3.2,
            hmin=10.0, hmax=13.5) -> tuple[dict, int]:
    """Drop a palm every ``spacing`` m of arc on BOTH verges of the masked stretch. ``terr(x,z)`` samples
    ground height; ``offset`` sits the palm this far beyond the road edge (on the parkway). ``on_mask[i]``
    selects which centreline vertices get palms. Returns ({'PALMTRUNK':..,'PALMFROND':..}, count)."""
    trunk = {"vertices": [], "tris": [], "uvs": []}
    frond = {"vertices": [], "tris": [], "uvs": []}
    acc = spacing
    count = 0
    for i in range(len(centerline) - 1):
        x, _y, z = centerline[i]
        dx = centerline[i + 1][0] - x
        dz = centerline[i + 1][2] - z
        acc += math.hypot(dx, dz)
        if not on_mask[i] or acc < spacing:
            continue
        acc = 0.0
        L = math.hypot(dx, dz) or 1.0
        nx, nz = -dz / L, dx / L                           # left road normal
        half = widths[i] / 2.0
        for side in (1, -1):
            ox = x + nx * side * (half + offset)
            oz = z + nz * side * (half + offset)
            h = hmin + (hmax - hmin) * ((i * 7) % 11) / 10.0
            yaw = i * 1.7 + (0.0 if side > 0 else math.pi)
            gy = terr(ox, oz)
            _add_trunk(ox, oz, gy, h, trunk)
            _add_fronds(ox, oz, gy + h, h, yaw, frond)
            count += 1
    return {"PALMTRUNK": trunk, "PALMFROND": frond}, count
