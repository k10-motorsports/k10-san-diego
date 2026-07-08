"""Re-tag the loop's per-vertex road width from OSM lane counts (data/road_lanes.cache.json).

The loop shipped at a flat default_width_m, which is far too tight for the primary arterials it runs on
(College/Navajo/Jackson/Lake Murray Blvd carry 2-5 lanes, and 6-9 at the intersection turn-pockets). Here
each centreline vertex is matched to its nearest lane-tagged OSM segment; width = lanes*LANE + shoulders,
smoothed. Because the fat lane counts sit right at the junctions, this widens the INTERSECTIONS too.

Writes widths back into data/centerline.local.json and data/centerline.geojson.

    python -m scripts.gps.road_widths <project-dir>
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

LANE_M = 3.5
SHOULDER_M = 3.0          # kerb-to-kerb margin beyond the travel lanes (both sides)
MIN_W, MAX_W = 13.0, 30.0


def _seg_dist_lanes(pt, segs):
    """Nearest OSM segment to (lon,lat) → its lane count (segments carry (a, b, lanes))."""
    best_d, best_l = 1e18, None
    px, py = pt
    for ax, ay, bx, by, lanes in segs:
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 == 0:
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        cx, cy = ax + t * dx, ay + t * dy
        d = (px - cx) ** 2 + (py - cy) ** 2
        if d < best_d:
            best_d, best_l = d, lanes
    return best_l


def build(project_dir: str | Path) -> dict:
    project_dir = Path(project_dir)
    data = project_dir / "data"
    segs_raw = json.loads((data / "road_lanes.cache.json").read_text())
    cfg = json.loads((project_dir / "track.config.json").read_text())
    # Divided surface streets tag each carriageway (oneway) with only its own lanes, so we double them to
    # read as the whole boulevard. A freeway loop is ONE wide carriageway we actually drive, so DON'T
    # double it (route.divided_double=false) — its own lane count is the width.
    double = cfg.get("route", {}).get("divided_double", True)
    # flatten to lane-tagged planar segments (lon,lat); lanes default by highway class when untagged
    HW_DEFAULT = {"motorway": 5, "trunk": 4, "primary": 4, "secondary": 3, "tertiary": 2,
                  "residential": 2, "unclassified": 2}
    segs = []
    for w in segs_raw:
        try:
            lanes = int(w.get("lanes")) if w.get("lanes") else HW_DEFAULT.get(w.get("hw"), 2)
        except ValueError:
            lanes = HW_DEFAULT.get(w.get("hw"), 2)
        # These arterials are DIVIDED: the OSM segment is one carriageway (oneway) tagged with only that
        # carriageway's lanes. Our ribbon should read as the whole boulevard, so double a oneway
        # carriageway's lanes to approximate the full cross-section (both directions + median).
        if double and w.get("oneway") == "yes":
            lanes *= 2
        g = w["geom"]
        for i in range(len(g) - 1):
            segs.append((g[i][0], g[i][1], g[i + 1][0], g[i + 1][1], lanes))

    gj = json.loads((data / "centerline.geojson").read_text())
    full = next(f for f in gj["features"] if f["properties"].get("kind") == "full")
    coords = full["geometry"]["coordinates"]

    raw = []
    for lon, lat in coords:
        lanes = _seg_dist_lanes((lon, lat), segs) or 2
        raw.append(max(MIN_W, min(MAX_W, lanes * LANE_M + SHOULDER_M)))
    # median-then-mean smooth so widths don't step abruptly between segments (keeps intersection bulges
    # but rounds their shoulders); ~15-vertex (≈45 m) windows.
    def smooth(a, win, fn):
        h = win // 2
        return [fn(a[max(0, i - h):i + h + 1]) for i in range(len(a))]
    med = smooth(raw, 15, lambda s: sorted(s)[len(s) // 2])
    widths = smooth(med, 11, lambda s: sum(s) / len(s))
    widths = [round(w, 2) for w in widths]

    full["properties"]["widths_m"] = widths
    full["properties"]["default_width_m"] = round(sum(widths) / len(widths), 1)
    (data / "centerline.geojson").write_text(json.dumps(gj))

    local = json.loads((data / "centerline.local.json").read_text())
    if len(local.get("widths_m", [])) == len(widths):
        local["widths_m"] = widths
        (data / "centerline.local.json").write_text(json.dumps(local))
    stats = {"n": len(widths), "min": min(widths), "max": max(widths),
             "mean": round(sum(widths) / len(widths), 1)}
    print(f"[road_widths] {stats}  (was flat 12.0)")
    return stats


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m scripts.gps.road_widths <project-dir>")
    build(sys.argv[1])


if __name__ == "__main__":
    main()
