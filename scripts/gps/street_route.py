"""Route a NAMED street along the real OSM drivable network — end to end, as driven.

Why not just take the ways named X and chain them (``merge_ways``)? Because real arterials break that:
divided roads double both carriageways into an out-and-back, names change across freeway interchanges
(70th St becomes Lake Murray Blvd over I-8), and endpoint-stitching dies at any gap — so the "longest
chain" silently truncates the street. Every "road that should connect but doesn't" bug in the San Diego
loop traced back to that.

Here instead: build ONE graph of the whole drivable network (including ramps/links), find the two
farthest-apart nodes that carry the street's name, and Dijkstra between them with named edges cheap and
everything else penalized. The result follows the street's real pavement its whole length, picks a single
carriageway through divided sections, and uses real link roads where the name momentarily changes — it
matches reality by construction. ``extend_to_target`` then continues a street's end along real pavement
to meet another road (e.g. 70th St through the I-8 interchange up to the loop) — never a fake bridge.

The network is cached to a JSON file so repeated builds don't hammer Overpass (which rate-limits hard).
"""

from __future__ import annotations

import heapq
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

Vertex = tuple[float, float]  # (lon, lat)

DRIVABLE = ("motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|"
            "tertiary|tertiary_link|residential|unclassified|living_street|road")
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OFF_NAME_PENALTY = 6.0   # cost multiplier for edges NOT carrying the street's name
SNAP = 6                  # node key decimals (~0.1 m)


def _hav(a: Vertex, b: Vertex) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = math.radians(b[1] - a[1]), math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fetch_network(bbox_swne: tuple[float, float, float, float], cache: Path | None = None,
                  *, retries: int = 2) -> list[dict]:
    """All drivable ways in the bbox: ``[{name, highway, geom:[(lon,lat)]}]``. Cached to ``cache``."""
    if cache and Path(cache).exists():
        return json.loads(Path(cache).read_text())
    s, w, n, e = bbox_swne
    q = f'[out:json][timeout:60];way["highway"~"^({DRIVABLE})$"]({s},{w},{n},{e});out geom;'
    body = urllib.parse.urlencode({"data": q}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        for m in MIRRORS:
            try:
                req = urllib.request.Request(m, data=body, headers={"User-Agent": "prodrive-ac-builder"})
                payload = json.load(urllib.request.urlopen(req, timeout=75))
                ways = [{"name": el.get("tags", {}).get("name"),
                         "highway": el["tags"].get("highway"),
                         "geom": [(g["lon"], g["lat"]) for g in el.get("geometry") or []]}
                        for el in payload.get("elements", [])
                        if el.get("type") == "way" and el.get("geometry")]
                if ways and cache:
                    Path(cache).write_text(json.dumps(ways))
                if ways:
                    return ways
            except Exception as ex:  # 429/504/timeout — next mirror
                last = ex
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"all Overpass mirrors failed: {last!r}")


class StreetGraph:
    """Drivable-network graph with named-street routing."""

    def __init__(self, ways: list[dict]):
        self.adj: dict[Vertex, list[tuple[Vertex, float, str | None]]] = defaultdict(list)
        self.name_nodes: dict[str, set[Vertex]] = defaultdict(set)
        for way in ways:
            nm = way.get("name")
            geom = way["geom"]
            for i in range(len(geom) - 1):
                a = (round(geom[i][0], SNAP), round(geom[i][1], SNAP))
                b = (round(geom[i + 1][0], SNAP), round(geom[i + 1][1], SNAP))
                if a == b:
                    continue
                d = _hav(a, b)
                self.adj[a].append((b, d, nm))
                self.adj[b].append((a, d, nm))
                if nm:
                    self.name_nodes[nm].add(a)
                    self.name_nodes[nm].add(b)

    def nearest_node(self, pt: Vertex) -> Vertex:
        return min(self.adj, key=lambda nd: (nd[0] - pt[0]) ** 2 + (nd[1] - pt[1]) ** 2)

    def _dijkstra(self, src: Vertex, dst: Vertex, prefer: str | None) -> list[Vertex]:
        dist = {src: 0.0}
        prev: dict[Vertex, Vertex] = {}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                break
            if d > dist.get(u, 9e18):
                continue
            for v, w, nm in self.adj[u]:
                cost = w if (prefer is None or nm == prefer) else w * OFF_NAME_PENALTY
                nd = d + cost
                if nd < dist.get(v, 9e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return []
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        path.reverse()
        return path

    def street_line(self, name: str) -> list[Vertex]:
        """The street's full drivable line: farthest pair of its named nodes, name-weighted Dijkstra.

        Ends are picked from the street's LARGEST connected named component only — a stray fragment of
        the same name across a bbox edge would otherwise drag the route on an off-name detour through
        side streets to reach it (Mission Gorge Rd did exactly that at the fetch corner).
        """
        nodes = sorted(self.name_nodes.get(name, set()))  # sorted: deterministic ends/direction run-to-run
        if len(nodes) < 2:
            return []
        # connected components over name-only edges, scored by total on-name length
        seen: set[Vertex] = set()
        best_comp: list[Vertex] = []
        best_len = -1.0
        for start in nodes:
            if start in seen:
                continue
            comp, comp_len, stack = [], 0.0, [start]
            seen.add(start)
            while stack:
                u = stack.pop()
                comp.append(u)
                for v, wlen, nm in self.adj[u]:
                    if nm == name:
                        comp_len += wlen / 2  # each edge visited from both ends
                        if v not in seen:
                            seen.add(v)
                            stack.append(v)
            if comp_len > best_len:
                best_comp, best_len = comp, comp_len
        # farthest pair within that component (subsample big streets — endpoints are what matter)
        sub = best_comp[:: max(1, len(best_comp) // 400)]
        best = (sub[0], sub[-1], -1.0)
        for i, a in enumerate(sub):
            for b in sub[i + 1:]:
                d = (a[0] - b[0]) ** 2 + ((a[1] - b[1]) * 1.19) ** 2  # rough lat weighting
                if d > best[2]:
                    best = (a, b, d)
        return self._dijkstra(best[0], best[1], prefer=name)

    def extend_to_target(self, line: list[Vertex], target: Vertex, *, max_m: float = 900.0) -> list[Vertex]:
        """Continue the closer END of ``line`` along real pavement to ``target`` (unweighted route).

        Used when a street's named pavement stops short of the road it meets in reality (name changes
        through an interchange). Returns the extended line, or the original if already there / too far.
        """
        tn = self.nearest_node(target)
        d0, d1 = _hav(line[0], tn), _hav(line[-1], tn)
        if min(d0, d1) < 15.0:
            return line
        if min(d0, d1) > max_m:
            return line
        end = line[0] if d0 <= d1 else line[-1]
        ext = self._dijkstra(self.nearest_node(end), tn, prefer=None)
        if not ext:
            return line
        return ext[::-1] + line if d0 <= d1 else line + ext
