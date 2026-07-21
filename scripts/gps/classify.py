"""Tag each network edge with an OSM road class + street name (nearest-way vote).

The KML directions give us the drivable geometry; OSM tells us what KIND of road each edge is so the
mesh can pick a width and the scenery pass can pick a furniture set (freeway = SRP-style barriers and
gantries; surface streets = palms + suburban dressing). We fetch every ``highway`` way in the network
bbox once, index its segments in local metres, and for each edge sample points and vote the nearest
way's class/name within a tolerance. Writes ``road_class``, ``name``, ``width_m`` back into
``data/network.geojson``. Stdlib-only (urllib via overpass helper).
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.config import load_config  # noqa: E402
from scripts.gps.overpass import OVERPASS_URL, USER_AGENT  # noqa: E402

# OSM highway tag -> normalized class used for width + furniture
_CLASS = {
    "motorway": "motorway", "motorway_link": "motorway",
    "trunk": "trunk", "trunk_link": "trunk",
    "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "secondary", "tertiary_link": "secondary",
    "residential": "residential", "living_street": "residential",
    "unclassified": "residential",
    "service": "service",
}


def _fetch_highways(bbox, retries=4):
    south, west, north, east = bbox
    q = (f"[out:json][timeout:120];"
         f'(way["highway"]({south},{west},{north},{east}););out geom;')
    # mirror fallback + 429-aware backoff live in trace.osm._post
    from scripts.trace.osm import _post
    return _post(q, retries=retries)


def classify(project_dir: str | Path) -> None:
    proj = Path(project_dir)
    cfg = load_config(proj)
    net = cfg.raw.get("network", {})
    widths = net.get("class_widths_m", {})
    default_w = float(cfg.default_width_m)
    # Real widths from lane count: US freeway lane ~3.66 m (12 ft) + paved shoulders. Config-overridable.
    lane_w = float(net.get("lane_width_m", 3.66))
    shoulder_main = float(net.get("shoulder_main_m", 3.5))   # outer(~3) + inner(~0.5) shoulder
    shoulder_ramp = float(net.get("shoulder_ramp_m", 2.0))

    # An osm-network was fetched from motorway/motorway_link ONLY, so EVERY edge is a freeway. Restrict the
    # nearest-way vote to freeway OSM ways so a ramp running beside a frontage road is never mislabelled a
    # surface street (which would give it curbs floating on ground the network doesn't have).
    osm_net = cfg.source.get("type") == "osm-network"

    netp = proj / "data" / "network.geojson"
    fc = json.loads(netp.read_text())
    F = fc["features"]
    lat0 = fc["properties"]["ref_lat"]; lon0 = fc["properties"]["ref_lon"]
    kx = math.cos(math.radians(lat0)) * 111320.0; ky = 110540.0
    to_m = lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)

    lons = [c[0] for f in F for c in f["geometry"]["coordinates"]]
    lats = [c[1] for f in F for c in f["geometry"]["coordinates"]]
    m = 0.003
    bbox = (min(lats) - m, min(lons) - m, max(lats) + m, max(lons) + m)
    print(f"fetching OSM highways in bbox {tuple(round(b,4) for b in bbox)} ...")
    data = _fetch_highways(bbox)

    # index OSM segments into a spatial hash (local metres)
    CELL = 40.0
    from collections import defaultdict
    buckets: dict[tuple[int, int], list] = defaultdict(list)
    nways = 0
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        hw = tags.get("highway")
        if hw is None:
            continue
        cls = _CLASS.get(hw)
        if cls is None:
            continue  # skip footways, cycleways, paths, etc.
        if osm_net and cls not in ("motorway", "trunk"):
            continue  # osm-network is freeway-only: don't let surface streets win the class vote
        name = tags.get("ref") if cls in ("motorway", "trunk") else tags.get("name")
        name = name or tags.get("name") or tags.get("ref")
        is_link = str(hw).endswith("_link")   # ramp/connector -> narrower than the mainline carriageway
        try:
            layer = int(float(tags.get("layer") or 0))   # OSM bridge stacking level (0=grade, 1/2/3=flyover)
        except (TypeError, ValueError):
            layer = 0
        try:
            lanes = int(float(tags.get("lanes"))) if tags.get("lanes") else 0   # real lane count (per carriageway)
        except (TypeError, ValueError):
            lanes = 0
        geom = [to_m(g["lon"], g["lat"]) for g in el.get("geometry") or []]
        nways += 1
        for a, b in zip(geom, geom[1:]):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            # register the segment in the cells its midpoint + endpoints touch
            for px, py in (a, b, mid):
                buckets[(int(px // CELL), int(py // CELL))].append((a, b, cls, name, is_link, layer, lanes))
    print(f"indexed {nways} OSM ways into {len(buckets)} cells")

    def _seg_dist(p, a, b):
        ax, ay = a; bx, by = b; px, py = p
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 < 1e-9:
            return math.hypot(px - ax, py - ay), None
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t

    TOL = 28.0
    LAYER_TOL = 8.0    # tight: lock a vertex to its OWN way's layer, not a stacked crossing way's
    counts = defaultdict(int)
    for f in F:
        coords = f["geometry"]["coordinates"]
        pm = [to_m(lon, lat) for lon, lat in coords]
        # sample ~ every 25 m
        votes_cls = defaultdict(float)
        votes_name = defaultdict(float)
        link_votes = 0
        mainline_votes = 0
        lane_samples = []
        acc = 0.0
        sample_pts = [pm[0]]
        for a, b in zip(pm, pm[1:]):
            acc += math.hypot(b[0] - a[0], b[1] - a[1])
            if acc >= 25.0:
                sample_pts.append(b); acc = 0.0
        sample_pts.append(pm[-1])
        for p in sample_pts:
            ci, cj = int(p[0] // CELL), int(p[1] // CELL)
            best_d, best = TOL, None
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for (a, b, cls, name, is_link, _layer, lanes) in buckets.get((ci + di, cj + dj), ()):
                        d, _t = _seg_dist(p, a, b)
                        if d < best_d:
                            best_d, best = d, (cls, name, is_link, lanes)
            if best is not None:
                votes_cls[best[0]] += 1
                if best[1]:
                    votes_name[best[1]] += 1
                if best[2]:
                    link_votes += 1
                else:
                    mainline_votes += 1
                if best[3]:
                    lane_samples.append(best[3])
        if votes_cls:
            cls = max(votes_cls, key=votes_cls.get)
            name = max(votes_name, key=votes_name.get) if votes_name else None
        elif osm_net:
            cls, name = "motorway", None       # osm-network edge with no freeway match nearby -> still a ramp
        else:
            cls, name = "residential", None   # unmatched (off-network connector) -> treat as small street
        # A freeway edge made mostly of *_link ways is a RAMP/interchange connector: keep it freeway-class
        # (so it gets walls + furniture) but render it at the narrower ramp width, not a mainline carriageway.
        is_ramp = cls in ("motorway", "trunk") and link_votes > mainline_votes
        counts["ramp" if is_ramp else cls] += 1
        f["properties"]["road_class"] = cls
        f["properties"]["is_ramp"] = is_ramp
        f["properties"]["name"] = name
        # REAL LANE COUNT (median of the nearest OSM ways) -> real width = lanes*lane_w + shoulders. Falls
        # back to the class width table when OSM has no lanes tag (some ramps). Drives markings + ribbon width.
        lanes = 0
        if lane_samples:
            lane_samples.sort(); lanes = lane_samples[len(lane_samples) // 2]
        f["properties"]["lanes"] = lanes
        if lanes:
            sh = shoulder_ramp if is_ramp else shoulder_main
            f["properties"]["width_m"] = round(lanes * lane_w + sh, 2)
        else:
            wkey = "ramp" if is_ramp else cls
            f["properties"]["width_m"] = float(widths.get(wkey, widths.get(cls, widths.get("default", default_w))))

        # PER-VERTEX BRIDGE LAYER: lock onto THIS edge's own underlying OSM way with a TIGHT tolerance
        # (LAYER_TOL) so a flyover deck reads its layer (1/2/3) while the road crossing directly beneath
        # stays layer 0 -> build_network_mesh raises the deck and the two grade-separate (no at-grade
        # collision, no wall trapping the car at interchanges). Only stored when the edge is bridged.
        prof = []
        any_layer = False
        for p in pm:
            ci, cj = int(p[0] // CELL), int(p[1] // CELL)
            bd, bl = LAYER_TOL, 0
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for (a, b, _cls, _name, _link, layer, _lanes) in buckets.get((ci + di, cj + dj), ()):
                        d, _t = _seg_dist(p, a, b)
                        if d < bd:
                            bd, bl = d, layer
            prof.append(bl)
            if bl:
                any_layer = True
        if any_layer:
            f["properties"]["layer_profile"] = prof
        else:
            f["properties"].pop("layer_profile", None)

    fc["properties"]["class_counts"] = dict(counts)
    netp.write_text(json.dumps(fc), encoding="utf-8")
    print("edge class counts:", dict(counts))
    # length per class
    by = defaultdict(float)
    for f in F:
        by[f["properties"]["road_class"]] += f["properties"]["length_m"]
    print("km per class:", {k: round(v / 1000, 1) for k, v in sorted(by.items(), key=lambda x: -x[1])})


if __name__ == "__main__":
    classify(sys.argv[1] if len(sys.argv) > 1 else "projects/san-diego-cruise")
