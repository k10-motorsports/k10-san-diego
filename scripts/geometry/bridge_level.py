"""Hold roads LEVEL across real OSM bridges (bare-earth DEMs have no deck).

3DEP is bare-earth: a bridge deck isn't in it, so a road crossing a freeway samples the freeway/trench
UNDERNEATH and sags to near-freeway level (College Ave over I-8 sat 3 m above the lanes). Fix: for each
road point that lies on a real OSM bridge span (way["bridge"]["highway"], matched by road NAME so we don't
touch a road that passes UNDER a freeway-on-a-bridge), arc-length-interpolate its height between the two
abutments — a straight, level deck, no mid-span dip. Run AFTER project, BEFORE mesh:

    python -m scripts.geometry.bridge_level projects/san-diego-loop

Reads data/bridges.cache.json (fetch once: way["bridge"]["highway"] over the terrain bbox) and edits
data/centerline.local.json (+ connectors.local.json if present) in place.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

DRIVABLE = {"motorway", "trunk", "primary", "secondary", "tertiary",
            "residential", "unclassified", "living_street"}


def _seg_d(p, a, b):
    """Point-to-segment distance in metres (lon/lat, local planar)."""
    la = math.radians((a[1] + b[1]) / 2)
    mx, my = 111_320 * math.cos(la), 110_574
    ax, ay = (a[0] - p[0]) * mx, (a[1] - p[1]) * my
    bx, by = (b[0] - p[0]) * mx, (b[1] - p[1]) * my
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _flatten(points_xyz, lonlat, bsegs, tag, near_m=16.0):
    on = [any(_seg_d(ll, a, b) < near_m for a, b in bsegs) for ll in lonlat]
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j + 1 < len(on) and on[j + 1]:
                j += 1
            runs.append((i, j)); i = j + 1
        else:
            i += 1
    for a, b in runs:
        lo, hi = max(0, a - 1), min(len(points_xyz) - 1, b + 1)
        ya, yb = points_xyz[lo][1], points_xyz[hi][1]
        dcum = [0.0]
        for k in range(lo + 1, hi + 1):
            dcum.append(dcum[-1] + math.dist(points_xyz[k][::2], points_xyz[k - 1][::2]))
        tot = dcum[-1] or 1.0
        for idx, k in enumerate(range(lo, hi + 1)):
            points_xyz[k][1] = ya + (yb - ya) * (dcum[idx] / tot)
        print(f"  [{tag}] leveled bridge pts {a}-{b} ({hi-lo} pts) between abutments {ya:.0f}->{yb:.0f} m")
    return len(runs)


def build(project_dir: str | Path) -> dict:
    project_dir = Path(project_dir)
    data = project_dir / "data"
    bcache = data / "bridges.cache.json"
    if not bcache.exists():
        print("[bridge_level] no data/bridges.cache.json — skipping (fetch way[bridge][highway] first)")
        return {"bridges": 0}
    bridges = json.loads(bcache.read_text())
    from collections import defaultdict
    bseg_by_name: dict = defaultdict(list)
    for br in bridges:
        if br.get("hw") in DRIVABLE and br.get("name"):
            g = br["geom"]
            for i in range(len(g) - 1):
                bseg_by_name[br["name"]].append((g[i], g[i + 1]))

    loc = json.loads((data / "centerline.local.json").read_text())
    lon0, lat0 = loc["origin"]["lon"], loc["origin"]["lat"]
    m_lon = 111_320 * math.cos(math.radians(lat0))
    m_lat = 110_574
    total = 0

    # Loop: match ALL drivable bridges (the loop only overlaps its own roads' bridges).
    gj = json.loads((data / "centerline.geojson").read_text())
    loop_ll = [tuple(c) for c in next(f for f in gj["features"]
                                      if f["properties"].get("kind") == "full")["geometry"]["coordinates"]]
    all_seg = [s for segs in bseg_by_name.values() for s in segs]
    if len(loop_ll) == len(loc["points_xyz_m"]):
        print("[bridge_level] loop:")
        total += _flatten(loc["points_xyz_m"], loop_ll, all_seg, "loop")
        (data / "centerline.local.json").write_text(json.dumps(loc))

    conn_path = data / "connectors.local.json"
    if conn_path.exists():
        cl = json.loads(conn_path.read_text())
        changed = False
        for c in cl.get("connectors", []):
            p = c["points_xyz_m"]
            ll = [(lon0 + x / m_lon, lat0 + z / m_lat) for x, _, z in p]
            n = _flatten(p, ll, all_seg, c["name"])
            total += n
            changed = changed or n > 0
        if changed:
            conn_path.write_text(json.dumps(cl))
    print(f"[bridge_level] leveled {total} bridge span(s)")
    return {"bridges": total}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m scripts.geometry.bridge_level <project-dir>")
    build(sys.argv[1])


if __name__ == "__main__":
    main()
