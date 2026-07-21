"""Merge N My Maps "Directions" KML/KMZ exports into ONE deduped, connected road NETWORK.

Unlike ``centerline.py`` (which stitches an ordered list of roads into a single closed lap), this
builds a *graph*: a freeroam street network where streets retraced across several direction exports
collapse into shared edges, and shared endpoints become real intersections you can turn through.

Pipeline:
  1. Parse every LineString from every source KML/KMZ (coords are already road-snapped by Google).
  2. Greedily CLUSTER vertices within ``snap_grid_m`` into shared nodes (spatial-hashed). A street
     driven by two different routes lands on the same node sequence, so it dedupes.
  3. Cut each polyline into LINKS between consecutive distinct nodes, carrying the real sub-geometry.
     Dedupe links by unordered node-pair (the retraced-street collapse).
  4. MERGE degree-2 chains: a node touched by exactly two links is not an intersection, so its two
     links concatenate into one longer edge. Real nodes (degree != 2) stay as junctions.
  5. Smooth interior points (junctions pinned) and resample each edge to ``resample_m`` spacing.
  6. Emit ``data/network.geojson`` (one Feature per edge, lon/lat) + ``data/network_preview.svg``.

Stdlib + math only; reads all knobs from ``track.config.json`` ``network`` block. Reproducible.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.config import load_config  # noqa: E402
from scripts.gps.kml import parse_kml  # noqa: E402


# --- local planar projection (equirectangular about a reference) so distances are in metres ----
def _projector(lat0: float, lon0: float):
    kx = math.cos(math.radians(lat0)) * 111320.0
    ky = 110540.0
    to_m = lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)
    to_ll = lambda x, y: (lon0 + x / kx, lat0 + y / ky)
    return to_m, to_ll


def _hav(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres between two (x,y) planar points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


class _NodeGrid:
    """Greedy spatial clustering: snap a stream of points to shared cluster centres within ``r``."""

    def __init__(self, r: float):
        self.r = r
        self.cell = r
        self.buckets: dict[tuple[int, int], list[int]] = {}
        self.cx: list[float] = []
        self.cy: list[float] = []
        self.cn: list[int] = []  # how many points contributed (running mean)

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def assign(self, x: float, y: float) -> int:
        ci, cj = self._key(x, y)
        best, best_d2 = -1, self.r * self.r
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for idx in self.buckets.get((ci + di, cj + dj), ()):
                    d2 = (x - self.cx[idx]) ** 2 + (y - self.cy[idx]) ** 2
                    if d2 <= best_d2:
                        best, best_d2 = idx, d2
        if best >= 0:
            n = self.cn[best] + 1            # running mean keeps the node near the true street centre
            self.cx[best] += (x - self.cx[best]) / n
            self.cy[best] += (y - self.cy[best]) / n
            self.cn[best] = n
            return best
        idx = len(self.cx)
        self.cx.append(x); self.cy.append(y); self.cn.append(1)
        self.buckets.setdefault((ci, cj), []).append(idx)
        return idx


def _resample(pts: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    """Evenly resample a polyline (planar metres) to ``step`` spacing, keeping both endpoints."""
    if len(pts) < 2:
        return pts
    segs = list(zip(pts, pts[1:]))
    lengths = [_hav(a, b) for a, b in segs]
    total = sum(lengths)
    if total < 1e-9:
        return [pts[0], pts[-1]]
    out = [pts[0]]
    cum = 0.0
    target = step
    for (a, b), L in zip(segs, lengths):
        if L < 1e-9:
            continue
        while target <= cum + L + 1e-9 and target < total:
            t = (target - cum) / L
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            target += step
        cum += L
    if _hav(out[-1], pts[-1]) > 1e-6:
        out.append(pts[-1])
    return out


def _smooth(pts: list[tuple[float, float]], iters: int) -> list[tuple[float, float]]:
    """Moving-average smooth interior points; pin the two endpoints (they are junctions)."""
    for _ in range(max(0, iters)):
        if len(pts) < 3:
            break
        new = [pts[0]]
        for i in range(1, len(pts) - 1):
            ax, ay = pts[i - 1]; bx, by = pts[i]; cx, cy = pts[i + 1]
            new.append(((ax + 2 * bx + cx) / 4.0, (ay + 2 * by + cy) / 4.0))
        new.append(pts[-1])
        pts = new
    return pts


def build_network(project_dir: str | Path) -> dict:
    proj = Path(project_dir)
    cfg = load_config(proj)
    net = cfg.raw.get("network", {})
    snap = float(net.get("snap_grid_m", 11.0))
    step = float(net.get("resample_m", 4.0))
    min_edge = float(net.get("min_edge_m", 18.0))
    smooth_iters = int(net.get("smooth_iters", 3))
    clip_radius = float(net.get("clip_radius_m", 0) or 0)   # 0 = no clip; else truncate to <r of home
    home_ll = (cfg.location["lon"], cfg.location["lat"])

    lines: list[list[tuple[float, float]]] = []
    src_type = cfg.source.get("type")
    if src_type == "osm-network":
        # Fetch a whole freeway box straight from OSM: every mainline carriageway AND every ramp /
        # interchange connector (all motorway_link ways) inside source.bbox. No KML — the map is only a
        # selection aid (CLAUDE.md first principle); real geometry comes from Overpass.
        from scripts.gps.overpass import fetch_highways_by_class
        bbox = tuple(cfg.source["bbox"])   # (south, west, north, east)
        classes = cfg.source.get("highways") or ["motorway", "motorway_link"]
        refs = cfg.source.get("refs")      # restrict mainlines to these OSM refs (excludes I-805 etc.)
        ways = fetch_highways_by_class(bbox, classes, refs=refs)
        lines = [w["geom"] for w in ways]
        by_hw: dict[str, int] = {}
        for wobj in ways:
            by_hw[wobj["highway"]] = by_hw.get(wobj["highway"], 0) + 1
        print(f"osm-network: fetched {len(ways)} ways in bbox {bbox} — {by_hw}")
        if not lines:
            raise SystemExit("osm-network: Overpass returned no ways for the bbox/classes")
    else:
        kml_files = cfg.source.get("kml_files") or [cfg.source.get("kml")]
        for rel in kml_files:
            for feat in parse_kml(proj / rel):
                if feat["type"] == "line" and len(feat["coords"]) >= 2:
                    lines.append(feat["coords"])
        if not lines:
            raise SystemExit("no LineStrings found in source KML files")

    # OSM AUGMENT (optional, ``network.osm_augment``) — for networks that must include streets the
    # KML directions never traced: the full drivable grid within ``bbox_margin_m`` of home, plus
    # named roads (regex, matched over the KML extent). OSM and KML retrace the same streets with
    # slightly different geometry; the node-snap grid dedupes them like any retraced KML leg.
    aug = net.get("osm_augment")
    if aug:
        from scripts.trace.osm import fetch_drivable, fetch_named
        m = float(aug.get("bbox_margin_m", 600.0))
        grid_ways = []
        for cl in aug.get("centers") or [list(home_ll)]:
            dlat = m / 111_132.0
            dlon = m / (111_132.0 * math.cos(math.radians(cl[1])))
            grid_ways += fetch_drivable((cl[1] - dlat, cl[0] - dlon, cl[1] + dlat, cl[0] + dlon))
        lines += [w["geom"] for w in grid_ways]
        named_ways = []
        roads = aug.get("roads") or []
        if roads:
            kv = [c for ln in lines for c in ln]
            pad = 0.002
            net_bbox = (min(c[1] for c in kv) - pad, min(c[0] for c in kv) - pad,
                        max(c[1] for c in kv) + pad, max(c[0] for c in kv) + pad)
            # entries: "Name" (whole network bbox) or {"name": ..., "bbox": [s,w,n,e]} to bound a
            # road ("Navajo Road up to Jackson" = Navajo Rd with an east limit).
            plain = [r for r in roads if isinstance(r, str)]
            if plain:
                named_ways += fetch_named(net_bbox, plain)
            for r in roads:
                if isinstance(r, dict):
                    named_ways += fetch_named(tuple(r["bbox"]), [r["name"]])
            lines += [w["geom"] for w in named_ways]
        names_desc = ", ".join(r if isinstance(r, str) else r["name"] for r in roads)
        print(f"  osm augment: +{len(grid_ways)} grid ways around centers, "
              f"+{len(named_ways)} named-road ways ({names_desc})")

    # reference for the planar frame = mean of all vertices
    allv = [c for ln in lines for c in ln]
    lon0 = sum(c[0] for c in allv) / len(allv)
    lat0 = sum(c[1] for c in allv) / len(allv)
    to_m, to_ll = _projector(lat0, lon0)

    grid = _NodeGrid(snap)
    # links keyed by unordered node pair -> representative planar geometry (dedupes retraced streets)
    links: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for ln in lines:
        seq: list[tuple[int, tuple[float, float]]] = []  # (node_id, planar pt)
        for lon, lat in ln:
            p = to_m(lon, lat)
            nid = grid.assign(*p)
            if not seq or seq[-1][0] != nid:
                seq.append((nid, p))
        for (na, pa), (nb, pb) in zip(seq, seq[1:]):
            if na == nb:
                continue
            key = (na, nb) if na < nb else (nb, na)
            if key in links:
                continue
            geom = [pa, pb] if (na < nb) else [pb, pa]  # store in key order (low->high node id)
            links[key] = geom

    # adjacency for chain merging
    from collections import defaultdict
    adj: dict[int, list[tuple[int, int]]] = defaultdict(list)  # node -> list of (other_node, link_key_index)
    keys = list(links.keys())
    for ki, (a, b) in enumerate(keys):
        adj[a].append((b, ki))
        adj[b].append((a, ki))
    degree = {n: len(v) for n, v in adj.items()}

    # KEEP LARGEST CONNECTED COMPONENT — drop orphan fragments. With mainlines restricted to our refs,
    # a parallel freeway's ramps (e.g. I-805's) lose their trunk and float free as small components;
    # our 4 freeways + every interchange ramp form one giant component (linked through the 4 stacks).
    keep_cc = bool(net.get("keep_largest_component", False))
    big: set[int] = set()
    if keep_cc:
        from collections import deque
        seen: set[int] = set()
        best_comp: list[int] = []
        for start in adj:
            if start in seen:
                continue
            q = deque([start]); seen.add(start); comp = [start]
            while q:
                u = q.popleft()
                for (v, _ki) in adj[u]:
                    if v not in seen:
                        seen.add(v); q.append(v); comp.append(v)
            if len(comp) > len(best_comp):
                best_comp = comp
        big = set(best_comp)
        print(f"  largest component: {len(big)}/{len(adj)} nodes kept (orphan ramps dropped)")

    def node_pt(n: int) -> tuple[float, float]:
        return (grid.cx[n], grid.cy[n])

    def link_geom(ki: int, frm: int) -> list[tuple[float, float]]:
        a, b = keys[ki]
        g = links[keys[ki]]
        return g if frm == a else list(reversed(g))

    used = [False] * len(keys)
    edges: list[dict] = []

    def walk_chain(start_node: int, first_ki: int) -> list[tuple[float, float]]:
        """Follow degree-2 nodes from start_node through first_ki, concatenating geometry."""
        pts: list[tuple[float, float]] = []
        cur, ki = start_node, first_ki
        while True:
            used[ki] = True
            g = link_geom(ki, cur)
            if pts:
                pts.extend(g[1:])
            else:
                pts.extend(g)
            a, b = keys[ki]
            nxt = b if cur == a else a
            # continue only through a true pass-through node (degree 2, link not used)
            if degree.get(nxt, 0) == 2:
                cont = [(o, k) for (o, k) in adj[nxt] if not used[k]]
                if len(cont) == 1:
                    cur, ki = nxt, cont[0][1]
                    continue
            break
        return pts

    # start chains at junctions/endpoints (degree != 2) first, then mop up pure loops. A chain never
    # leaves its component, so gating the start node on ``big`` keeps only the largest component.
    for n in [n for n in degree if degree[n] != 2]:
        if keep_cc and n not in big:
            continue
        for (other, ki) in list(adj[n]):
            if not used[ki]:
                edges.append({"pts": walk_chain(n, ki)})
    for ki in range(len(keys)):
        if not used[ki]:
            a, _b = keys[ki]
            if keep_cc and a not in big:
                continue
            edges.append({"pts": walk_chain(a, ki)})

    # CLIP: truncate every edge to the portion within clip_radius of home, splitting into contiguous
    # runs (so a freeway that only grazes the neighbourhood keeps just its near stretch, not its 19 km
    # tail). Bounds the whole track to a small, light extent.
    home_m = to_m(*home_ll) if clip_radius else None

    def _clip_runs(pts):
        if not clip_radius:
            return [pts]
        runs, cur = [], []
        for p in pts:
            if (p[0] - home_m[0]) ** 2 + (p[1] - home_m[1]) ** 2 <= clip_radius * clip_radius:
                cur.append(p)
            elif cur:
                runs.append(cur); cur = []
        if cur:
            runs.append(cur)
        return runs

    # smooth + resample, drop degenerate, record stats
    feats = []
    total_m = 0.0
    eid = 0
    for e in edges:
        for pts in _clip_runs(e["pts"]):
            if len(pts) < 2:
                continue
            L0 = sum(_hav(a, b) for a, b in zip(pts, pts[1:]))
            if L0 < min_edge:
                if _hav(pts[0], pts[-1]) < min_edge * 0.5:   # tiny stub
                    continue
            pts = _smooth(pts, smooth_iters)
            pts = _resample(pts, step)
            L = sum(_hav(a, b) for a, b in zip(pts, pts[1:]))
            total_m += L
            coords = [list(to_ll(x, y)) for x, y in pts]
            feats.append({
                "type": "Feature",
                "properties": {
                    "id": eid,
                    "kind": "edge",
                    "length_m": round(L, 1),
                    "point_count": len(coords),
                    "road_class": None,      # filled by scripts/gps/classify.py (OSM)
                    "name": None,
                    "width_m": None,
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            })
            eid += 1

    fc = {
        "type": "FeatureCollection",
        "properties": {
            "source": "kml-network",
            "ref_lon": lon0, "ref_lat": lat0,
            "edge_count": len(feats),
            "node_count": len(grid.cx),
            "total_length_m": round(total_m, 1),
            "snap_grid_m": snap, "resample_m": step,
        },
        "features": feats,
    }
    out = proj / "data" / "network.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc), encoding="utf-8")

    _write_preview(proj / "data" / "network_preview.svg", feats, to_m)
    print(f"network: {len(feats)} edges, {len(grid.cx)} nodes, {total_m/1000:.1f} km total")
    print(f"  wrote {out}")
    return fc


def _write_preview(path: Path, feats: list[dict], to_m) -> None:
    pts_xy = []
    for f in feats:
        pts_xy.append([to_m(lon, lat) for lon, lat in f["geometry"]["coordinates"]])
    xs = [p[0] for e in pts_xy for p in e]
    ys = [p[1] for e in pts_xy for p in e]
    if not xs:
        return
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    W = 1600
    H = max(1, int(W * (maxy - miny) / (maxx - minx)))
    sx = lambda x: (x - minx) / (maxx - minx) * W
    sy = lambda y: H - (y - miny) / (maxy - miny) * H
    paths = []
    for e in pts_xy:
        d = "M" + " L".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in e)
        paths.append(f'<path d="{d}" fill="none" stroke="#28d07a" stroke-width="1.3" opacity="0.75"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">'
           f'<rect width="{W}" height="{H}" fill="#10141a"/>' + "".join(paths) + "</svg>")
    path.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    build_network(sys.argv[1] if len(sys.argv) > 1 else "projects/san-diego-cruise")
