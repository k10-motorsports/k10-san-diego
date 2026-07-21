"""Synthetic GENTLE elevation for the FICTIONAL K10 inter-track world.

The tracks sit at FABRICATED compressed positions, so sampling real USGS 3DEP across the inter-track
space manufactures fake ~150 m climbs and garbage cells (a reservoir once floated to 23 km — see
network_elev history). Instead, define ONE gentle field: a plane that rises gently toward the far-WEST
Front Range (user: "a gentle slope TOWARD them"), so the tracks, the connectors, and the ground all
share a single consistent, drivable elevation. Each track keeps its OWN internal relief (built in its
own frame by the loop/aerial pipeline); this field only decides where each track's pad SITS in Y.

Consumed by: network_elev (connector road z + heightfield), merge_detailed (per-track seat height),
build_network_env (water + scatter seating via the field-derived heightfield). Pure stdlib; runs on
system python3. Only active when scenery.elevation.synthetic is truthy (other projects keep real 3DEP).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from scripts.config import load_config
from scripts.geometry.projection import _meters_per_degree

BLEND_M = 3000.0   # each track's base nudges the ground within ~this radius (low curvature -> no undulation)


def enabled(cfg) -> bool:
    return bool(((cfg.raw.get("scenery", {}) or {}).get("elevation", {}) or {}).get("synthetic", False))


def _origin(cfg):
    og = cfg.raw["origin"]
    return float(og["lon"]), float(og["lat"]), float(og["elev_m"])


def projector(cfg):
    """lon/lat -> K10 LOCAL (x, z) metres, matching build_network_mesh / merge_detailed (mirror-aware).
    Under mirror_x, +x = true WEST, so the plane rises with +x toward the western mountains."""
    lon0, lat0, _ = _origin(cfg)
    m_lon, m_lat = _meters_per_degree(lat0)
    sx = -1.0 if cfg.raw.get("mirror_x", True) else 1.0

    def to_xz(lon, lat):
        return (sx * (lon - lon0) * m_lon, (lat - lat0) * m_lat)
    return to_xz


def _params(cfg):
    el = (cfg.raw.get("scenery", {}) or {}).get("elevation", {}) or {}
    return (float(el.get("datum_base_m", 18.0)),
            float(el.get("west_rise_per_km_m", 1.25)),
            el.get("per_track_override", {}) or {})


def plane_y(cfg, x: float) -> float:
    """Gentle base height (m above the origin datum) at local x. +x = WEST -> rises toward the mountains."""
    datum, rise, _ = _params(cfg)
    return datum + rise * (x / 1000.0)


def resolve_track_bases(k10_dir) -> dict:
    """{slug: base_y_m} — each track's chosen gentle base height = the plane at its pad centre, unless
    scenery.elevation.per_track_override pins it. Small total spread (gentle) vs the 190 m real-3DEP spread."""
    k10 = Path(k10_dir); cfg = load_config(k10)
    from scripts.ac.merge_detailed import track_footprints
    _, _, ov = _params(cfg)
    pads = track_footprints(k10)
    field, _, _ = make_field(k10)                    # each track seats on the REAL ground at its pad centre
    return {slug: (float(ov[slug]) if slug in ov else field(p["cx"], p["cz"])) for slug, p in pads.items()}


def make_field(k10_dir):
    """Return (field_local, to_xz, elev0):
       field_local(x, z) -> GROUND height (m above elev0) anywhere in the inter-track world;
       to_xz(lon, lat)   -> K10 local (x, z);
       elev0             -> origin datum, so callers can emit ABSOLUTE z = field_local + elev0.
    Reads the REAL, despiked, grade-capped elevation grid network_elev built from USGS 3DEP along the actual
    roads (data/heightfield.npy) and bilinearly samples it — so the connectors, the tracks, and the ground
    ALL ride the same real (but drivable) surface. Falls back to a flat datum pre-network_elev."""
    k10 = Path(k10_dir); cfg = load_config(k10)
    to_xz = projector(cfg); lon0, lat0, elev0 = _origin(cfg)
    m_lon, m_lat = _meters_per_degree(lat0)
    sx = -1.0 if cfg.raw.get("mirror_x", True) else 1.0
    hf = (k10 / "data" / "heightfield.npy"); mf = (k10 / "data" / "heightfield.meta.json")
    if not (hf.exists() and mf.exists()):
        datum = float(((cfg.raw.get("scenery", {}) or {}).get("elevation", {}) or {}).get("datum_base_m", 16.0))
        return (lambda x, z: datum), to_xz, elev0
    from scripts.geometry.build_mesh import read_npy
    grid = read_npy(hf); meta = json.loads(mf.read_text())
    s, w, n, e = meta["bbox_swne"]; nx, ny = meta["nx"], meta["ny"]
    gy = meta["spacing_m"] / 111_000.0
    gx = meta["spacing_m"] / (111_000.0 * math.cos(math.radians((s + n) / 2)))

    def field_local(x, z):
        lon = lon0 + sx * x / m_lon; lat = lat0 + z / m_lat
        fj = (n - lat) / gy; fi = (lon - w) / gx
        j0 = min(ny - 2, max(0, int(fj))); i0 = min(nx - 2, max(0, int(fi)))
        tj = min(1.0, max(0.0, fj - j0)); ti = min(1.0, max(0.0, fi - i0))
        top = grid[j0][i0] * (1 - ti) + grid[j0][i0 + 1] * ti
        bot = grid[j0 + 1][i0] * (1 - ti) + grid[j0 + 1][i0 + 1] * ti
        return top * (1 - tj) + bot * tj - elev0
    return field_local, to_xz, elev0
