"""Project the loop's EXTRA road lines (connector streets + split-carriageway lines) into the same local
frame as the main loop, each carrying its OWN real 3DEP elevation, and write ``data/connectors.local.json``.

The Blender-first loop (build_loop_blend.py) sweeps ONE closed centerline. Everything else — the Del Cerro
sub-loop, and the opposing carriageway of a divided road that rides a different grade than the one the main
ring picked — is an *extra line*: a named polyline, routed on the real OSM network (already extracted into
centerline.geojson as ``kind:"connector"`` by centerline.py), sampled for real point-precise 3DEP so a
neighbourhood street on the Del Cerro hill or the low side of a split arterial carries its true elevation.

Runs AFTER projection (needs the loop's shared origin + elev0 from centerline.local.json) and BEFORE
bridge_level (which then levels any real bridge spans on these lines) and the Blender rebuild.

    python -m scripts.geometry.extra_lines project        # samples live 3DEP for each connector

Output ``connectors.local.json`` = {"connectors": [{name, kind, points_xyz_m:[[x,y,z]], widths_m:[...]}]}
— the exact shape bridge_level.py + build_loop_blend.py read.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from scripts.config import load_config
from scripts.elevation import usgs_3dep
from scripts.elevation.heightfield import smooth_open
from scripts.geometry.projection import _meters_per_degree
from scripts.gps.centerline import merge_ways, resample, polyline_length

RESAMPLE_M = 3.0
DEFAULT_CONNECTOR_WIDTH_M = 10.0   # neighbourhood street: two lanes + parking/shoulder
MAX_GRADE = 0.11                   # cap street grade (Del Cerro hill is steep, but keep it drivable)


def _despike_smooth_cap(z: list[float], spacing_m: float, *, mean_m: float = 45.0,
                        max_grade: float = MAX_GRADE) -> list[float]:
    """Open-ended (non-loop) median de-spike + mean smooth + grade cap for one extra line, so a DEM notch
    at an underpass or a tree return doesn't launch the car and the street stays drivable."""
    n = len(z)
    if n < 3:
        return list(z)
    # median-3 de-spike (kills single-sample V-notches, preserves real steps)
    med = [z[0]] + [sorted(z[i - 1:i + 2])[1] for i in range(1, n - 1)] + [z[-1]]
    sm = smooth_open(med, max(3, round(mean_m / spacing_m)) | 1)
    # forward + backward grade cap so no consecutive step exceeds max_grade*spacing
    cap = max_grade * spacing_m
    for i in range(1, n):
        if sm[i] - sm[i - 1] > cap:
            sm[i] = sm[i - 1] + cap
        elif sm[i - 1] - sm[i] > cap:
            sm[i] = sm[i - 1] - cap
    for i in range(n - 2, -1, -1):
        if sm[i] - sm[i + 1] > cap:
            sm[i] = sm[i + 1] + cap
    return sm


def _line_spacing_m(coords, m_lon, m_lat) -> float:
    if len(coords) < 2:
        return RESAMPLE_M
    tot = 0.0
    for i in range(1, len(coords)):
        tot += math.hypot((coords[i][0] - coords[i - 1][0]) * m_lon,
                          (coords[i][1] - coords[i - 1][1]) * m_lat)
    return tot / (len(coords) - 1)


def build(project_dir: str | Path) -> dict:
    project_dir = Path(project_dir)
    data = project_dir / "data"
    cfg = load_config(project_dir)
    route = cfg.raw.get("route", {})

    connectors_cfg = route.get("connectors", {}) or {}
    if not connectors_cfg:
        print("[extra_lines] no route.connectors configured — nothing to do")
        return {"connectors": 0}

    # Pull connector ways straight from the cached OSM network (offline) — self-contained, so the main
    # loop's centerline.local.json is never re-derived. Each connector = one merged, resampled polyline.
    cache = json.loads((data / "network.cache.json").read_text(encoding="utf-8"))
    byname: dict[str, list] = {}
    for w in cache:
        nm = w.get("name")
        if nm:
            byname.setdefault(nm, []).append([(x[0], x[1]) for x in w["geom"]])

    loc = json.loads((data / "centerline.local.json").read_text(encoding="utf-8"))
    o = loc["origin"]
    lon0, lat0, elev0 = o["lon"], o["lat"], o["elev_m"]
    m_lon, m_lat = _meters_per_degree(lat0)

    # loop bbox (+ margin) in lon/lat, to clip a connector's off-map sprawl (e.g. Del Cerro Blvd runs
    # ~1.4 km WEST past the mapped loop) down to the longest contiguous in-bounds run.
    lxs = [p[0] for p in loc["points_xyz_m"]]; lzs = [p[2] for p in loc["points_xyz_m"]]
    MARGIN = 150.0
    lon_lo = lon0 + (min(lxs) - MARGIN) / m_lon; lon_hi = lon0 + (max(lxs) + MARGIN) / m_lon
    lat_lo = lat0 + (min(lzs) - MARGIN) / m_lat; lat_hi = lat0 + (max(lzs) + MARGIN) / m_lat

    def _clip_bbox(coords):
        inb = [lon_lo <= lo <= lon_hi and lat_lo <= la <= lat_hi for lo, la in coords]
        best = (0, 0); i = 0; n = len(coords)
        while i < n:
            if inb[i]:
                j = i
                while j < n and inb[j]:
                    j += 1
                if j - i > best[1] - best[0]:
                    best = (i, j)
                i = j
            else:
                i += 1
        return coords[best[0]:best[1]]

    # per-connector width overrides (route.connector_widths_m: {name: m}); else route.connector_width_m; else default
    cw_over = route.get("connector_widths_m", {}) or {}
    cw_def = float(route.get("connector_width_m", DEFAULT_CONNECTOR_WIDTH_M))

    out_conns = []
    for cname, rnames in connectors_cfg.items():
        merged = merge_ways([w for rn in rnames for w in byname.get(rn, [])])
        merged = _clip_bbox(merged) if merged else []
        coords = resample(merged, RESAMPLE_M) if merged else []
        name = cname
        if len(coords) < 2:
            print(f"[extra_lines] {name}: no geometry for {rnames} in cache — skipped")
            continue
        z_raw = usgs_3dep.sample_points(coords)
        spacing = _line_spacing_m(coords, m_lon, m_lat)
        z = _despike_smooth_cap(z_raw, spacing)
        pts = [[round((lon - lon0) * m_lon, 2), round(zz - elev0, 2), round((lat - lat0) * m_lat, 2)]
               for (lon, lat), zz in zip(coords, z)]
        w = float(cw_over.get(name, cw_def))
        out_conns.append({"name": name, "kind": "connector",
                          "points_xyz_m": pts, "widths_m": [w] * len(pts)})
        print(f"[extra_lines] {name}: {len(pts)} pts, z {min(z):.1f}..{max(z):.1f} m (elev0 {elev0:.1f}), width {w:.1f} m")

    (data / "connectors.local.json").write_text(
        json.dumps({"connectors": out_conns}), encoding="utf-8")
    print(f"[extra_lines] wrote {len(out_conns)} connector line(s) -> data/connectors.local.json")
    return {"connectors": len(out_conns)}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m scripts.geometry.extra_lines <project-dir>")
    build(sys.argv[1])


if __name__ == "__main__":
    main()
