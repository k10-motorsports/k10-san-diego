"""Elevation for a road NETWORK (freeroam), not a single lap.

Samples USGS 3DEP along EVERY edge of ``data/network.geojson`` (so each street carries real z), smooths
each edge open-ended, and samples a coarse terrain grid over the whole network bbox for the ground.

Outputs (data/):
  - network.elevation.json  — per-edge {id, z_raw_m, z_smooth_m}
  - heightfield.npy + heightfield.meta.json — terrain grid over the bbox (row 0 = north)
  - elevation_profile.svg   — quick min/max banding preview

Reuses the 3DEP batch sampler and the .npy writer from the single-loop tool. Stdlib only.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.elevation import synthetic_field  # noqa: E402
from scripts.elevation import usgs_3dep  # noqa: E402
from scripts.elevation.heightfield import smooth_profile, smooth_open, write_npy  # noqa: E402

GRID_SPACING_M = 55.0     # coarse landscape grid (upsampled near roads at mesh time)
GRID_MARGIN_M = 220.0
MEDIAN_M = 33.0
MEAN_M = 45.0
MAX_GRADE = 0.05          # cap road slope at 5% — no "narnia" launches, and a real mountain climb (e.g.
                          # the compressed grade UP to Aspen) becomes a gentle, drivable grade.


def _smooth_edge(z_raw, spacing_m):
    """Open-ended de-spike + smooth for one edge (median kills DEM notches at over/underpasses)."""
    n = len(z_raw)
    if n < 3:
        return list(z_raw)
    mw = max(3, round(MEDIAN_M / spacing_m)) | 1
    h = mw // 2
    med = [sorted(z_raw[max(0, i - h):min(n, i + h + 1)])[(min(n, i + h + 1) - max(0, i - h)) // 2]
           for i in range(n)]
    sw = max(3, round(MEAN_M / spacing_m)) | 1
    sm = smooth_open(med, sw)
    # grade cap: clamp consecutive-vertex slope so no road exceeds MAX_GRADE. Turns steep real terrain
    # (mountain spokes) into gentle drivable grades and removes any residual spike -> no launches.
    cap = MAX_GRADE * spacing_m
    out = [sm[0]]
    for i in range(1, len(sm)):
        dz = max(-cap, min(cap, sm[i] - out[-1]))
        out.append(out[-1] + dz)
    return out


def _despike_smooth_cap(z_raw, spacing_m, mean_m, max_grade, seg_d=None):
    """Median de-spike + HEAVY smooth + tight grade cap for a REAL road profile — the real Colorado rolls
    come through, but DEM notches/spikes and steep pitches that launch the car do not. The grade cap uses
    the ACTUAL per-segment distance (seg_d[i] = metres from vertex i-1 to i) so it holds even where the
    resampled vertices are closer than nominal (a fixed spacing over-estimates the allowed step -> launches).
    Two forward+backward cap passes so a long steep real climb is spread gently, not clipped into a step."""
    n = len(z_raw)
    if n < 3:
        return list(z_raw)
    mw = max(3, round(MEDIAN_M / spacing_m)) | 1; h = mw // 2
    med = [sorted(z_raw[max(0, i - h):min(n, i + h + 1)])[(min(n, i + h + 1) - max(0, i - h)) // 2]
           for i in range(n)]
    out = smooth_open(med, max(3, round(mean_m / spacing_m)) | 1)
    d = seg_d if seg_d is not None else [spacing_m] * n
    for _ in range(2):
        for i in range(1, len(out)):                     # forward
            cap = max_grade * max(0.5, d[i])
            out[i] = out[i - 1] + max(-cap, min(cap, out[i] - out[i - 1]))
        for i in range(len(out) - 2, -1, -1):            # backward (symmetric — no directional bias)
            cap = max_grade * max(0.5, d[i + 1])
            out[i] = out[i + 1] + max(-cap, min(cap, out[i] - out[i + 1]))
    return out


def _build_synthetic(proj: Path, data: Path, fc: dict, F: list, cfg) -> dict:
    """REAL road elevations, made DRIVABLE. Sample USGS 3DEP along every connector (these ARE real roads),
    median-despike + heavily smooth + grade-cap (~3.5%) so the real rolls survive but launches don't, then
    build the ground grid by INVERSE-DISTANCE blending those real road heights (+ track pad centres) so the
    terrain FOLLOWS the roads everywhere — no fill-min float. Connectors, tracks and ground all ride this one
    real, drivable surface. Optional scenery.elevation.compress (<1) shrinks the spread toward the median."""
    import numpy as np
    from scripts.ac.merge_detailed import track_footprints
    elev0 = float(cfg.raw["origin"]["elev_m"])
    to_xz = synthetic_field.projector(cfg)
    el = (cfg.raw.get("scenery", {}) or {}).get("elevation", {}) or {}
    compress = float(el.get("compress", 1.0)); max_grade = float(el.get("max_grade", 0.035))
    mean_m = float(el.get("smooth_m", 90.0))
    spacing = float(fc["properties"].get("resample_m", 4.0))

    # 1) one batched 3DEP fetch for all connector vertices
    flat, spans = [], []
    for f in F:
        c = f["geometry"]["coordinates"]; spans.append((len(flat), len(c))); flat.extend((lo, la) for lo, la in c)
    print(f"sampling REAL 3DEP for {len(flat)} connector vertices ...")
    zall = usgs_3dep.sample_points(flat)
    valid = sorted(v for v in zall if v is not None)
    zref = valid[len(valid) // 2] if valid else elev0

    # 2) per-edge despike/smooth/cap (+ optional compress toward the median), emit connector z + collect anchors
    edges_elev = []; a_lon = []; a_lat = []; a_z = []
    zmin, zmax = 1e9, -1e9
    for f, (off, ln) in zip(F, spans):
        cc = f["geometry"]["coordinates"]
        xy = [to_xz(lo, la) for lo, la in cc]
        seg_d = [0.0] + [math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]) for i in range(1, len(xy))]
        zr = [zref if v is None else v for v in zall[off:off + ln]]
        zs = _despike_smooth_cap(zr, spacing, mean_m, max_grade, seg_d)
        if compress != 1.0:
            zs = _despike_smooth_cap([zref + compress * (v - zref) for v in zs], spacing, mean_m, max_grade, seg_d)
        zmin = min(zmin, min(zs)); zmax = max(zmax, max(zs))
        edges_elev.append({"id": f["properties"]["id"], "z_raw_m": [round(v, 2) for v in zr],
                           "z_smooth_m": [round(v, 2) for v in zs]})
        for (lo, la), zz in zip(f["geometry"]["coordinates"], zs):
            a_lon.append(lo); a_lat.append(la); a_z.append(zz)
    (data / "network.elevation.json").write_text(json.dumps({
        "spacing_m": spacing, "synthetic": True, "real": True, "edge_count": len(F),
        "z_min_m": round(zmin, 1), "z_max_m": round(zmax, 1), "edges": edges_elev}), encoding="utf-8")

    # 3) ground grid = IDW blend of the real road heights so the terrain follows the roads (numpy, chunked)
    lons = [c[0] for f in F for c in f["geometry"]["coordinates"]]
    lats = [c[1] for f in F for c in f["geometry"]["coordinates"]]
    midlat = (min(lats) + max(lats)) / 2
    mlat = GRID_MARGIN_M / 111_000.0; mlon = GRID_MARGIN_M / (111_000.0 * math.cos(math.radians(midlat)))
    s, w, n, e = min(lats) - mlat, min(lons) - mlon, max(lats) + mlat, max(lons) + mlon
    gy = GRID_SPACING_M / 111_000.0; gx = GRID_SPACING_M / (111_000.0 * math.cos(math.radians(midlat)))
    ny = int((n - s) / gy) + 1; nx = int((e - w) / gx) + 1
    ax = np.array([to_xz(lo, la)[0] for lo, la in zip(a_lon, a_lat)])
    az = np.array([to_xz(lo, la)[1] for lo, la in zip(a_lon, a_lat)])
    av = np.array(a_z)
    st = max(1, len(ax) // 3000); ax, az, av = ax[::st], az[::st], av[::st]   # subsample anchors for speed
    # grid cell centres in local metres
    gj = np.arange(ny); gi = np.arange(nx)
    gx_l = np.array([to_xz(w + i * gx, midlat)[0] for i in gi])
    gz_l = np.array([to_xz(w, n - j * gy)[1] for j in gj])
    grid = np.empty((ny, nx), dtype=np.float32)
    P2 = 1.0e6   # IDW smoothing floor (1 km) -> low-curvature ground, no bullseyes around anchors
    for j in range(ny):
        dz2 = (gz_l[j] - az) ** 2
        dx = gx_l[:, None] - ax[None, :]
        wgt = 1.0 / (dx * dx + dz2[None, :] + P2)
        grid[j] = (wgt @ av) / wgt.sum(axis=1)
    write_npy(data / "heightfield.npy", grid.tolist())
    (data / "heightfield.meta.json").write_text(json.dumps(
        {"bbox_swne": [round(s, 6), round(w, 6), round(n, 6), round(e, 6)],
         "nx": nx, "ny": ny, "spacing_m": GRID_SPACING_M, "margin_m": GRID_MARGIN_M, "row0": "north"}),
        encoding="utf-8")
    stats = {"z_min_m": round(zmin, 1), "z_max_m": round(zmax, 1), "range_m": round(zmax - zmin, 1),
             "grid": f"{nx}x{ny}@{GRID_SPACING_M}m", "real": True, "cap_grade": max_grade, "compress": compress}
    print("network elevation done (REAL 3DEP, despiked+capped, road-conformed ground):", stats)
    return stats


def build(project_dir: str | Path) -> dict:
    proj = Path(project_dir)
    data = proj / "data"
    fc = json.loads((data / "network.geojson").read_text())
    F = fc["features"]
    spacing = float(fc["properties"].get("resample_m", 4.0))

    from scripts.config import load_config  # noqa: E402
    _cfg = load_config(proj)
    if synthetic_field.enabled(_cfg):
        return _build_synthetic(proj, data, fc, F, _cfg)

    # --- per-edge elevation: one big batched fetch for all edge vertices, then split back ----
    flat_pts = []
    spans = []
    for f in F:
        c = f["geometry"]["coordinates"]
        spans.append((len(flat_pts), len(c)))
        flat_pts.extend((lon, lat) for lon, lat in c)
    print(f"sampling 3DEP for {len(flat_pts)} edge vertices across {len(F)} edges ...")
    z_all = usgs_3dep.sample_points(flat_pts)

    edges_elev = []
    zmin, zmax = 1e9, -1e9
    for f, (off, ln) in zip(F, spans):
        zr = z_all[off:off + ln]
        zs = _smooth_edge(zr, spacing)
        zmin = min(zmin, min(zs)); zmax = max(zmax, max(zs))
        edges_elev.append({"id": f["properties"]["id"], "z_raw_m": [round(v, 2) for v in zr],
                           "z_smooth_m": [round(v, 2) for v in zs]})
    (data / "network.elevation.json").write_text(json.dumps({
        "spacing_m": spacing, "median_m": MEDIAN_M, "mean_m": MEAN_M,
        "edge_count": len(F), "z_min_m": round(zmin, 1), "z_max_m": round(zmax, 1),
        "edges": edges_elev,
    }), encoding="utf-8")

    # --- coarse terrain grid over the whole bbox (config can OVERRIDE to include peaks + the coast) ----
    lons = [c[0] for f in F for c in f["geometry"]["coordinates"]]
    lats = [c[1] for f in F for c in f["geometry"]["coordinates"]]
    midlat = (min(lats) + max(lats)) / 2
    from scripts.config import load_config  # noqa: E402
    terr = (load_config(proj).raw.get("scenery", {}) or {}).get("terrain", {}) or {}
    if terr.get("bbox_swne"):
        s, w, n, e = terr["bbox_swne"]
        print(f"terrain bbox OVERRIDE from config: {terr['bbox_swne']}")
    else:
        mlat = GRID_MARGIN_M / 111_000.0
        mlon = GRID_MARGIN_M / (111_000.0 * math.cos(math.radians(midlat)))
        s, w, n, e = min(lats) - mlat, min(lons) - mlon, max(lats) + mlat, max(lons) + mlon
    gy = GRID_SPACING_M / 111_000.0
    gx = GRID_SPACING_M / (111_000.0 * math.cos(math.radians(midlat)))
    ny = int((n - s) / gy) + 1
    nx = int((e - w) / gx) + 1
    points = [(w + i * gx, n - j * gy) for j in range(ny) for i in range(nx)]
    # NEAR-ROAD MASK: only query USGS for grid cells within BUFFER of a road. On a wide multi-spoke map
    # (compressed I-70 E/W + I-25 S) the full bbox is mostly empty land far from any road, and those far
    # cells are pruned from the mesh anyway (build_network_mesh grass corridor-mask, 500 m). Sampling only
    # near-road cells keeps this from ballooning into millions of USGS requests; far cells fill flat.
    BUFFER_M = 700.0
    from collections import defaultdict
    bdlat = BUFFER_M / 111_000.0
    bdlon = BUFFER_M / (111_000.0 * math.cos(math.radians(midlat)))
    rb = defaultdict(list)
    for lo, la in flat_pts:
        rb[(int(lo / bdlon), int(la / bdlat))].append((lo, la))

    def _near_road(lo, la):
        ci, cj = int(lo / bdlon), int(la / bdlat)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for rlo, rla in rb.get((ci + di, cj + dj), ()):
                    if abs(lo - rlo) <= bdlon and abs(la - rla) <= bdlat:
                        return True
        return False
    sample_idx = [k for k, (lo, la) in enumerate(points) if _near_road(lo, la)]
    print(f"sampling terrain grid {nx}x{ny} @ {GRID_SPACING_M}m — {len(sample_idx)}/{len(points)} "
          f"near-road cells (rest filled flat) ...")
    sampled = usgs_3dep.sample_points([points[k] for k in sample_idx])
    fill = min(sampled) if sampled else float(round(zmin, 1))
    flat = [fill] * len(points)
    for k, z in zip(sample_idx, sampled):
        flat[k] = z
    grid = [flat[j * nx:(j + 1) * nx] for j in range(ny)]
    write_npy(data / "heightfield.npy", grid)
    meta = {"bbox_swne": [round(s, 6), round(w, 6), round(n, 6), round(e, 6)],
            "nx": nx, "ny": ny, "spacing_m": GRID_SPACING_M, "margin_m": GRID_MARGIN_M, "row0": "north"}
    (data / "heightfield.meta.json").write_text(json.dumps(meta), encoding="utf-8")

    stats = {"z_min_m": round(zmin, 1), "z_max_m": round(zmax, 1),
             "range_m": round(zmax - zmin, 1), "grid": f"{nx}x{ny}@{GRID_SPACING_M}m"}
    print("network elevation done:", stats)
    return stats


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "projects/san-diego-cruise")
