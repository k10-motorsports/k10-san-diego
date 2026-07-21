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

    # main-loop path (local X,Z) for split-carriageway proximity clipping
    main_xz = [(p[0], p[2]) for p in loc["points_xyz_m"]]

    def _chains(ways):
        segs = [list(w) for w in ways if len(w) >= 2]
        used = [False] * len(segs); out = []

        def key(p):
            return (round(p[0], 6), round(p[1], 6))
        for i, s in enumerate(segs):
            if used[i]:
                continue
            used[i] = True; ch = list(s); ext = True
            while ext:
                ext = False
                for j, t in enumerate(segs):
                    if used[j]:
                        continue
                    if key(ch[-1]) == key(t[0]):
                        ch += t[1:]
                    elif key(ch[-1]) == key(t[-1]):
                        ch += list(reversed(t))[1:]
                    elif key(ch[0]) == key(t[-1]):
                        ch = t[:-1] + ch
                    elif key(ch[0]) == key(t[0]):
                        ch = list(reversed(t))[:-1] + ch
                    else:
                        continue
                    used[j] = True; ext = True
            out.append(ch)
        return sorted(out, key=polyline_length, reverse=True)

    def _near_main(coords, reach_m):
        """Longest contiguous run of a chain whose points lie within reach_m of the main loop (in local m).
        Isolates the OPPOSING carriageway segment that parallels the loop from the rest of a long road."""
        xz = [((lo - lon0) * m_lon, (la - lat0) * m_lat) for lo, la in coords]
        near = []
        for x, z in xz:
            d = min((x - mx) ** 2 + (z - mz) ** 2 for mx, mz in main_xz[::2])
            near.append(d <= reach_m * reach_m)
        best = (0, 0); i = 0; n = len(coords)
        while i < n:
            if near[i]:
                j = i
                while j < n and near[j]:
                    j += 1
                if j - i > best[1] - best[0]:
                    best = (i, j)
                i = j
            else:
                i += 1
        return coords[best[0]:best[1]]

    def _mean_lat(coords):
        """Mean lateral distance (local m) of a chain's points from the main loop."""
        xz = [((lo - lon0) * m_lon, (la - lat0) * m_lat) for lo, la in coords]
        return sum(min(math.hypot(x - mx, z - mz) for mx, mz in main_xz[::2]) for x, z in xz) / len(xz)

    split_roads = route.get("split_carriageways", []) or []
    split_conns = {}
    narrow_idx: set = set()
    CLIP_REACH = float(route.get("split_reach_m", 45.0))
    OPP_MIN, OPP_MAX = 6.0, 40.0   # opposing carriageway sits this far (m) laterally from the loop's carriageway
    MIN_SEP = float(route.get("split_min_sep_m", 11.0))   # only add opposing where carriageways are this far apart
    for road in split_roads:
        chs = [c for c in _chains(byname.get(road, [])) if polyline_length(c) > 200]
        if len(chs) < 2:
            print(f"[extra_lines] split '{road}': not divided (only {len(chs)} carriageway) — skipped")
            continue
        # Clip every carriageway chain to the part running alongside the loop; the loop RIDES the nearest
        # (~1 m). The OPPOSING carriageway is the clipped run sitting OPP_MIN..OPP_MAX m laterally away.
        cands = []
        for c in chs:
            run = _near_main(c, CLIP_REACH)
            if polyline_length(run) < 120:
                continue
            cands.append((_mean_lat(run), run))
        opp = next((run for lat, run in sorted(cands) if OPP_MIN <= lat <= OPP_MAX), None)
        loop_run = next((run for lat, run in sorted(cands) if lat < OPP_MIN), None)
        if opp is None or loop_run is None:
            near = [f"{lat:.0f}m" for lat, _ in sorted(cands)]
            print(f"[extra_lines] split '{road}': no clean loop+opposing carriageway pair "
                  f"(runs at {near}) — skipped")
            continue
        # Keep only where the two carriageways are CLEANLY DIVIDED (>= MIN_SEP apart). Where a divided road
        # merges back to one (junctions, tapers), the carriageways converge and a separate opposing ribbon
        # would overlap the main deck — so drop those points and keep the longest well-separated run.
        opp_xz = [((lo - lon0) * m_lon, (la - lat0) * m_lat) for lo, la in opp]
        sep_ok = [min(math.hypot(x - mx, z - mz) for mx, mz in main_xz[::2]) >= MIN_SEP for x, z in opp_xz]
        best = (0, 0); i = 0; n = len(opp)
        while i < n:
            if sep_ok[i]:
                j = i
                while j < n and sep_ok[j]:
                    j += 1
                if j - i > best[1] - best[0]:
                    best = (i, j)
                i = j
            else:
                i += 1
        opp = opp[best[0]:best[1]]
        if polyline_length(opp) < 200:
            print(f"[extra_lines] split '{road}': no ≥200 m cleanly-divided run (>{MIN_SEP:.0f} m apart) — skipped")
            continue
        split_conns[road.lower().replace(" ", "_") + "_opp"] = opp
        # main-loop vertices that ride THIS road's carriageway -> narrow them to a single carriageway so the
        # double-wide ribbon doesn't overlap the opposing carriageway we're about to add beside it.
        lr_xz = [((lo - lon0) * m_lon, (la - lat0) * m_lat) for lo, la in loop_run]
        for i, (mx, mz) in enumerate(main_xz):
            if min((mx - lx) ** 2 + (mz - lz) ** 2 for lx, lz in lr_xz[::3]) < 12.0 ** 2:
                narrow_idx.add(i)
        print(f"[extra_lines] split '{road}': opposing carriageway {polyline_length(opp):.0f} m alongside loop")

    out_conns = []
    # opposing carriageways build first (single-carriageway width), then the configured connectors
    for cname, opp_coords in split_conns.items():
        coords = resample(opp_coords, RESAMPLE_M)
        if len(coords) < 2:
            continue
        z_raw = usgs_3dep.sample_points(coords)
        spacing = _line_spacing_m(coords, m_lon, m_lat)
        z = _despike_smooth_cap(z_raw, spacing)
        pts = [[round((lo - lon0) * m_lon, 2), round(zz - elev0, 2), round((la - lat0) * m_lat, 2)]
               for (lo, la), zz in zip(coords, z)]
        w = float(route.get("split_width_m", 11.0))
        out_conns.append({"name": cname, "kind": "carriageway",
                          "points_xyz_m": pts, "widths_m": [w] * len(pts)})
        print(f"[extra_lines] {cname}: {len(pts)} pts, z {min(z):.1f}..{max(z):.1f} m, width {w:.1f} m")

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

    payload = {"connectors": out_conns}
    if narrow_idx:
        payload["narrow_main"] = {"width_m": float(route.get("split_width_m", 11.0)),
                                  "idx": sorted(narrow_idx)}
    (data / "connectors.local.json").write_text(json.dumps(payload), encoding="utf-8")
    print(f"[extra_lines] wrote {len(out_conns)} extra line(s)"
          + (f" + {len(narrow_idx)} main-loop verts to narrow" if narrow_idx else "")
          + " -> data/connectors.local.json")
    return {"connectors": len(out_conns), "narrow": len(narrow_idx)}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m scripts.geometry.extra_lines <project-dir>")
    build(sys.argv[1])


if __name__ == "__main__":
    main()
