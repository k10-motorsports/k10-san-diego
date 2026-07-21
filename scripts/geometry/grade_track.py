"""Grade a race track ELEGANTLY: derive subtle, realistic corner banking (camber) from the racing line's
own curvature, and write it into ``road_profile.cambers`` — the hook profile.py already feeds to every
swept generator. A flat traced loop keeps reading dead-flat; real circuits have a touch of superelevation
in the corners (the road leans into the turn). This adds that, tastefully, from geometry — no hand-authoring.

What it does NOT do: vertical elevation. That comes from REAL USGS 3DEP (the ``elevation`` stage) baked
into the centerline Y — run that first; this script warns if a track has no ``centerline.elevation.json``.

Per corner it computes a radius from the smoothed curvature and banks by a subtle superelevation curve
(sharper corner -> a little more lean, capped), signed so the OUTSIDE edge lifts, ramped in/out. Small
wiggles and gentle sweepers are ignored. Researched, known banking (e.g. a banked oval) is applied from
``grading.overrides`` verbatim instead of the procedural curve.

    python -m scripts.geometry.grade_track projects/<slug>            # write cambers into the config
    python -m scripts.geometry.grade_track projects/<slug> --dry-run  # print, don't write

Config (``grading`` block in track.config.json, all optional):
  max_bank_deg (5.0)  bank_scale (210)  r_max_m (260)  min_corner_deg (24)  min_bank_deg (0.9)
  bank_sign (1)       overrides: [ {name, start_m, len_m, bank_deg, ramp_m} ]  (verbatim, e.g. a banked oval)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def _load_centerline_xz(proj: Path):
    """Return [(x, z)] horizontal racing-line points in the build's local metres + whether real elevation
    is present. Prefers data/centerline.local.json (the frame profile.py stations match)."""
    data = proj / "data"
    lp = data / "centerline.local.json"
    if lp.exists():
        loc = json.loads(lp.read_text())
        pts = [(p[0], p[2]) for p in loc["points_xyz_m"]]   # (x, z); y is elevation
        return pts
    gj = data / "centerline.geojson"
    if gj.exists():
        fc = json.loads(gj.read_text())
        coords = []
        for f in fc.get("features", fc if isinstance(fc, list) else []):
            g = f.get("geometry", {}) if isinstance(f, dict) else {}
            if g.get("type") == "LineString":
                coords = g["coordinates"]; break
        if coords:
            lat0 = sum(c[1] for c in coords) / len(coords)
            kx = math.cos(math.radians(lat0)) * 111320.0
            return [((c[0] - coords[0][0]) * kx, (c[1] - coords[0][1]) * 110540.0) for c in coords]
    raise SystemExit("no centerline.local.json or centerline.geojson — build the gps/project stages first")


def _stations_curvature(pts):
    """Per-vertex (station_m, signed_curvature 1/m). Curvature = turn angle / mean adjacent segment len;
    sign +ve for one turn direction (calibrated to the config bank_sign)."""
    n = len(pts)
    st = [0.0]
    for i in range(1, n):
        st.append(st[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    kappa = [0.0] * n
    for i in range(1, n - 1):
        ax, az = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        bx, bz = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        la = math.hypot(ax, az); lb = math.hypot(bx, bz)
        if la < 1e-6 or lb < 1e-6:
            continue
        cross = ax * bz - az * bx
        dot = ax * bx + az * bz
        ang = math.atan2(cross, dot)                 # signed turn angle (rad)
        kappa[i] = ang / ((la + lb) / 2.0)           # 1/m, signed
    return st, kappa


def _smooth(v, win):
    n = len(v)
    if win < 2 or n < 3:
        return list(v)
    h = win // 2
    return [sum(v[max(0, i - h):min(n, i + h + 1)]) / (min(n, i + h + 1) - max(0, i - h)) for i in range(n)]


def grade(proj: Path, *, dry_run=False) -> dict:
    cfg_path = proj / "track.config.json"
    cfg = json.loads(cfg_path.read_text())
    g = cfg.get("grading", {}) or {}
    max_bank = float(g.get("max_bank_deg", 4.0))       # subtle for a road course; ovals use overrides
    bank_scale = float(g.get("bank_scale", 160.0))     # bank_deg ~ bank_scale / radius, capped at max_bank
    r_max = float(g.get("r_max_m", 260.0))             # only corners tighter than this get banked
    min_deg = float(g.get("min_corner_deg", 24.0))     # ignore wiggles that don't add up to a real corner
    min_bank = float(g.get("min_bank_deg", 0.9))
    bank_sign = float(g.get("bank_sign", 1.0))
    overrides = g.get("overrides", []) or []

    if not (proj / "data" / "centerline.elevation.json").exists():
        print("  ⚠ no data/centerline.elevation.json — run the elevation stage first so the track uses "
              "REAL 3DEP height (otherwise it stays flat). Grading banking anyway.")

    pts = _load_centerline_xz(proj)
    st, kappa = _stations_curvature(pts)
    ks = _smooth(kappa, 7)
    total = st[-1]
    k_min = 1.0 / r_max

    # find contiguous corner runs where |curvature| exceeds the threshold
    cambers = []
    i = 1
    n = len(ks)
    cnum = 0
    while i < n - 1:
        if abs(ks[i]) < k_min:
            i += 1
            continue
        sign0 = 1 if ks[i] > 0 else -1
        j = i
        turn = 0.0
        kpeak = 0.0
        while j < n - 1 and abs(ks[j]) >= k_min * 0.6 and (1 if ks[j] > 0 else -1) == sign0:
            turn += kappa[j] * ((st[min(j + 1, n - 1)] - st[max(j - 1, 0)]) / 2.0)  # accumulate real angle
            if abs(ks[j]) > abs(kpeak):
                kpeak = ks[j]
            j += 1
        run_len = st[j - 1] - st[i]
        turn_deg = abs(math.degrees(turn))
        if turn_deg >= min_deg and run_len > 12.0 and abs(kpeak) > 1e-9:
            R = 1.0 / abs(kpeak)
            bank = min(max_bank, bank_scale / R)
            if bank >= min_bank:
                cnum += 1
                ramp = max(8.0, min(28.0, run_len / 3.0))
                cambers.append({
                    "name": f"corner_{cnum}",
                    "start_m": round(st[i], 1),
                    "len_m": round(run_len, 1),
                    "bank_deg": round(bank_sign * (1 if kpeak > 0 else -1) * bank, 2),
                    "ramp_m": round(ramp, 1),
                    "_radius_m": round(R, 1),
                    "_turn_deg": round(turn_deg, 1),
                })
        i = j + 1

    # researched / known banking (e.g. a banked oval) replaces the procedural curve verbatim
    for ov in overrides:
        cambers.append({**{k: ov[k] for k in ("name", "start_m", "len_m", "bank_deg", "ramp_m") if k in ov},
                        "_researched": True})

    rp = cfg.get("road_profile") or {}
    rp["cambers"] = cambers
    cfg["road_profile"] = rp
    banks = [c["bank_deg"] for c in cambers if c.get("bank_deg")]
    stats = {"lap_m": round(total, 1), "corners_banked": len(cambers),
             "bank_deg_range": [round(min(banks, default=0), 1), round(max(banks, default=0), 1)],
             "mean_abs_bank": round(sum(abs(b) for b in banks) / len(banks), 2) if banks else 0.0,
             "researched": sum(1 for c in cambers if c.get("_researched"))}
    if dry_run:
        print(json.dumps(cambers, indent=1))
    else:
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"grade_track {proj.name}: {json.dumps(stats)}")
    return stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    grade(Path(args[0]) if args else Path("projects/sand-creek-raceway"),
          dry_run="--dry-run" in sys.argv)
