"""Dress the freeroam NETWORK by road class: freeway furniture (SRP-style) on the freeways, California
palms + roadside trees on the surface streets, streetlights, and Lake Murray as water.

At ~154 km this MUST stay light, so vegetation is BILLBOARDS (crossed cards on an atlas) and guardrail
is a single swept ribbon — not thousands of instanced high-poly props. The only instanced real prop is
the mined SRP light pole (92 verts), placed sparsely on freeways. Everything is double-sided so the
mirror_x frame needs no winding bookkeeping. Groups are tiled under AC's 65,535-vert cap.

Outputs data/environment.obj (+ environment.mtl). Consumes network.geojson / .local / .elevation +
heightfield. Run: python -m scripts.environment.build_network_env projects/san-diego-cruise
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.config import load_config  # noqa: E402
from scripts.geometry import ribbon  # noqa: E402
from scripts.geometry.build_mesh import read_npy, write_obj  # noqa: E402
from scripts.geometry.projection import _meters_per_degree  # noqa: E402

# Cap by TRIANGLES (not verts): the kn5 exporter expands to ~3 verts/tri then auto-splits >65,535,
# giving the halves DUPLICATE names — which makes AC drop the duplicate from the scene. Stay under
# ~21,800 tris/mesh so names remain unique.
TRI_CAP = 21000
FREEWAY = {"motorway", "trunk"}


def _ds(tris):
    """Double-side a triangle list (append reversed winding)."""
    return list(tris) + [(a, c, b) for a, b, c in tris]


def _merge(meshes):
    V, U, T = [], [], []
    for m in meshes:
        off = len(V)
        V.extend(m["vertices"]); U.extend(m.get("uvs") or [(0.0, 0.0)] * len(m["vertices"]))
        T.extend((a + off, b + off, c + off) for a, b, c in m["tris"])
    return {"vertices": V, "uvs": U, "tris": T}


def _pack(prefix, material, meshes, billboard=False):
    """Bin meshes into merged groups each under TRI_CAP triangles, with a unique name per group (so the
    exporter never auto-splits into duplicate-named nodes)."""
    groups, bucket, tc, part = [], [], 0, 0
    for m in meshes:
        nt = len(m["tris"])
        if tc + nt > TRI_CAP and bucket:
            groups.append((f"{prefix}_{part}", material, _merge(bucket))); part += 1; bucket, tc = [], 0
        bucket.append(m); tc += nt
    if bucket:
        groups.append((f"{prefix}_{part}", material, _merge(bucket)))
    return groups


def _ground_local_sampler(data):
    """Bilinear sampler over the conformed local-XZ ground grid (data/ground.local.json). None if absent."""
    p = data / "ground.local.json"
    if not p.exists():
        return None
    g = json.loads(p.read_text())
    x0, z0, dx, dz, nx, ny, Y = g["x0"], g["z0"], g["dx"], g["dz"], g["nx"], g["ny"], g["y"]

    def sample(x, z):
        fi = (x - x0) / dx if dx else 0.0
        fj = (z - z0) / dz if dz else 0.0
        i0 = max(0, min(nx - 1, int(fi))); j0 = max(0, min(ny - 1, int(fj)))
        i1 = min(nx - 1, i0 + 1); j1 = min(ny - 1, j0 + 1)
        ti = max(0.0, min(1.0, fi - i0)); tj = max(0.0, min(1.0, fj - j0))
        a = Y[j0][i0] * (1 - ti) + Y[j0][i1] * ti
        b = Y[j1][i0] * (1 - ti) + Y[j1][i1] * ti
        return a * (1 - tj) + b * tj
    return sample


def _grid_sampler(grid, meta, elev0):
    s, w, n, e = meta["bbox_swne"]
    nx, ny, sp = meta["nx"], meta["ny"], meta["spacing_m"]
    gy = sp / 111_000.0
    gx = sp / (111_000.0 * math.cos(math.radians((s + n) / 2)))

    def y_at(lon, lat):
        j = min(ny - 1, max(0, int(round((n - lat) / gy))))
        i = min(nx - 1, max(0, int(round((lon - w) / gx))))
        return max(-50.0, min(600.0, grid[j][i] - elev0))   # clamp: never let a bad cell fling a prop/disc to the sky
    return y_at


def _billboard_cell(x, y, z, col, row, ncols, nrows, h, w, yaw):
    hw = w / 2
    c, s = math.cos(yaw), math.sin(yaw)
    ax, az = c * hw, s * hw
    bx, bz = -s * hw, c * hw
    verts = [(x - ax, y, z - az), (x + ax, y, z + az), (x + ax, y + h, z + az), (x - ax, y + h, z - az),
             (x - bx, y, z - bz), (x + bx, y, z + bz), (x + bx, y + h, z + bz), (x - bx, y + h, z - bz)]
    u0, u1 = col / ncols, (col + 1) / ncols
    vb, vt = (nrows - 1 - row) / nrows, (nrows - row) / nrows
    uvs = [(u0, vb), (u1, vb), (u1, vt), (u0, vt), (u0, vb), (u1, vb), (u1, vt), (u0, vt)]
    return {"vertices": verts, "uvs": uvs, "tris": _ds([(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)])}


def _guardrail(pts, *, half, h=0.85, off=0.5, tile_m=4.0):
    """A swept vertical guardrail ribbon along one side of an edge (W-beam metal). Double-sided."""
    V, U, T = [], [], []
    arc = 0.0
    rows = []
    for i in range(len(pts)):
        if i > 0:
            arc += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][2] - pts[i - 1][2])
        x, y, z = pts[i]
        a = pts[max(0, i - 1)]; b = pts[min(len(pts) - 1, i + 1)]
        tx, tz = b[0] - a[0], b[2] - a[2]; tl = math.hypot(tx, tz) or 1.0
        nx, nz = -tz / tl, tx / tl
        px, pz = x + nx * (half + off), z + nz * (half + off)
        r = len(V)
        V.append((px, y + 0.45, pz)); U.append((arc / tile_m, 0.0))
        V.append((px, y + 0.45 + h, pz)); U.append((arc / tile_m, 1.0))
        rows.append(r)
    for k in range(len(rows) - 1):
        a0, a1, b0, b1 = rows[k], rows[k] + 1, rows[k + 1], rows[k + 1] + 1
        T += [(a0, a1, b1), (a0, b1, b0)]
    return {"vertices": V, "uvs": U, "tris": _ds(T)}


def _gantry(x, y, z, tx, tz, half, *, h=6.4, post_w=0.32, beam_d=0.55):
    """An SRP-style overhead sign portal spanning the road at (x,z): two galvanized posts just outside
    each shoulder + a box beam across the top. Returns (metal_mesh, sign_panel_mesh). Double-sided."""
    nx, nz = -tz, tx                      # across-road normal (unit)
    span = half + 1.4
    def box(cx, cz, y0, y1, wx, wz):
        hx, hz = wx / 2, wz / 2
        c = [(cx - hx, cz - hz), (cx + hx, cz - hz), (cx + hx, cz + hz), (cx - hx, cz + hz)]
        V = [(px, y0, pz) for px, pz in c] + [(px, y1, pz) for px, pz in c]
        T = []
        for k in range(4):
            j = (k + 1) % 4
            T += [(k, j, j + 4), (k, j + 4, k + 4)]
        T += [(4, 5, 6), (4, 6, 7)]       # top cap
        return V, T
    Vm, Tm = [], []
    def add(V, T):
        off = len(Vm); Vm.extend(V); Tm.extend((a + off, b + off, c + off) for a, b, c in T)
    lx, lz = x + nx * span, z + nz * span
    rx, rz = x - nx * span, z - nz * span
    add(*box(lx, lz, y, y + h, post_w, post_w))          # left post
    add(*box(rx, rz, y, y + h, post_w, post_w))          # right post
    # beam across the top (a box from left to right post)
    bcx, bcz = x, z
    add(*box(bcx, bcz, y + h - beam_d, y + h, (span * 2) * abs(nx) + post_w, (span * 2) * abs(nz) + post_w))
    metal = {"vertices": Vm, "uvs": [(0.0, 0.0)] * len(Vm), "tris": _ds(Tm)}
    # green sign panel hung under the beam, facing along the road
    pw, ph = span * 1.1, 1.8
    py0 = y + h - beam_d - ph - 0.1
    pnx, pnz = tx, tz                      # panel faces down the road (driver sees it head-on)
    pc = (x, z)
    Vp = [(pc[0] - nx * pw, py0, pc[1] - nz * pw), (pc[0] + nx * pw, py0, pc[1] + nz * pw),
          (pc[0] + nx * pw, py0 + ph, pc[1] + nz * pw), (pc[0] - nx * pw, py0 + ph, pc[1] - nz * pw)]
    panel = {"vertices": Vp, "uvs": [(0, 0), (1, 0), (1, 1), (0, 1)], "tris": _ds([(0, 1, 2), (0, 2, 3)])}
    return metal, panel


def _water_disc(cx, cz, y, r, n=48):
    V = [(cx, y, cz)]; U = [(0.5, 0.5)]
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        V.append((cx + math.cos(a) * r, y, cz + math.sin(a) * r)); U.append((0.5 + 0.5 * math.cos(a), 0.5 + 0.5 * math.sin(a)))
    T = [(0, i, i + 1) for i in range(1, n + 1)]
    return {"vertices": V, "uvs": U, "tris": _ds(T)}


def _load_obj(path):
    V, T = [], []
    if not Path(path).exists():   # optional mined prop (e.g. SRP light pole) absent -> skip gracefully
        return V, T
    for ln in Path(path).read_text().splitlines():
        if ln.startswith("v "):
            _, x, y, z = ln.split()[:4]; V.append((float(x), float(y), float(z)))
        elif ln.startswith("f "):
            idx = [int(p.split("/")[0]) - 1 for p in ln.split()[1:]]
            for k in range(1, len(idx) - 1):
                T.append((idx[0], idx[k], idx[k + 1]))
    return V, T


def _place_obj(V, T, x, y, z, yaw=0.0, scale=1.0):
    c, s = math.cos(yaw), math.sin(yaw)
    nv = [(x + (vx * c - vz * s) * scale, y + vy * scale, z + (vx * s + vz * c) * scale) for vx, vy, vz in V]
    return {"vertices": nv, "uvs": [(0.0, 0.0)] * len(nv), "tris": _ds(T)}


def build(project_dir: str | Path) -> dict:
    proj = Path(project_dir)
    data = proj / "data"
    cfg = load_config(proj)
    scn = cfg.raw.get("scenery", {})
    ff = scn.get("freeway_furniture", {})
    palms_enabled = bool(scn.get("palms", True))   # SoCal palms; false for Colorado -> broadleaf only
    loc = json.loads((data / "network.local.json").read_text())
    o = loc["origin"]; lon0, lat0 = o["lon"], o["lat"]; elev0 = o["elev_m"]
    sx = -1.0 if loc.get("mirror_x", True) else 1.0
    m_lon, m_lat = _meters_per_degree(lat0)
    fc = json.loads((data / "network.geojson").read_text())
    ev = {e["id"]: e["z_smooth_m"] for e in json.loads((data / "network.elevation.json").read_text())["edges"]}

    grid = read_npy(data / "heightfield.npy")
    meta = json.loads((data / "heightfield.meta.json").read_text())
    y_at = _grid_sampler(grid, meta, elev0)

    def to_local(lon, lat, z):
        return (sx * (lon - lon0) * m_lon, z - elev0, (lat - lat0) * m_lat)

    # Prefer the CONFORMED ground (build_network_mesh) so props sit on the real grass mesh, not the raw
    # heightfield (which floats them wherever the terrain was conformed to a road / clamped for poke).
    _gl = _ground_local_sampler(data)

    def ground_at(x_local, z_local):
        return _gl(x_local, z_local) if _gl else y_at(lon0 + sx * x_local / m_lon, lat0 + z_local / m_lat)

    # shared viaduct lift (written by build_network_mesh) so freeway furniture rides the elevated deck
    via = {}
    vpath = data / "network.viaduct.json"
    if vpath.exists():
        via = json.loads(vpath.read_text())

    # project all edges + collect (class, pts, half) and a road-corridor rejecter
    edges = []
    for f in fc["features"]:
        c = f["geometry"]["coordinates"]; z = ev.get(f["properties"]["id"]) or [0.0] * len(c)
        if len(z) != len(c):
            z = (z + [z[-1]] * len(c))[:len(c)]
        pts = [to_local(lon, lat, zz) for (lon, lat), zz in zip(c, z)]
        lift = via.get(str(f["properties"]["id"]))
        if lift and len(lift) == len(pts):
            pts = [(x, y + lift[i], z2) for i, (x, y, z2) in enumerate(pts)]   # ride the viaduct deck
        w = float(f["properties"].get("width_m") or 9.0)
        edges.append((f["properties"]["road_class"], pts, w / 2.0))

    on_road = _corridor(edges, margin=float(scn.get("corridor_margin_m", 3.5)))
    # strict "on the asphalt" test (half + 1 m) — furniture sits at its OWN shoulder (half+1.8) so it
    # survives this, but a light/pole/gantry that lands on a CROSSING road at a junction is rejected.
    # This is the "no cruft on the road at intersections" guard.
    on_road_tight = _corridor(edges, margin=1.0)

    # track discs: reject any scatter/furniture/buildings/water that would land UNDER a merged detailed
    # track (Sand Creek, IMI, ...) — the network ground there is cleared + reconciled, so a network tree
    # or warehouse would float/bury. Footprints shared with build_network_mesh/merge_detailed.
    try:
        from scripts.ac.merge_detailed import track_footprints
        _pads = [(p["cx"], p["cz"], p["radius_m"]) for p in track_footprints(proj).values()]
    except Exception as _e:  # noqa: BLE001
        _pads = []; print(f"  (track pad reject skipped: {_e})")

    def in_pad(x, z):
        for px, pz, pr in _pads:
            if (x - px) ** 2 + (z - pz) ** 2 < pr * pr:
                return True
        return False

    # highway furniture toggles — the SRP freeway guardrails/gantries read wrong on open Colorado plains
    # (user: "guardrails look like shit"). Default off for cohesion with the kerbed race tracks; keep
    # the sparse light poles. Config: scenery.freeway_furniture.{guardrails,gantries}.
    want_guardrails = bool(ff.get("guardrails", True))
    want_gantries = bool(ff.get("gantries", True))
    want_streetlights = bool(scn.get("streetlights", True))   # rural Colorado connectors need no light-pole spam

    rnd = random.Random(2025)
    guardrails, palms, trees, posts, poles, gantry_metal, gantry_sign, bushes = [], [], [], [], [], [], [], []
    nbush = 0
    pole_V, pole_T = _load_obj(proj / "assets" / "props" / "freeway" / "srp_lightpole_lamp101.obj")

    light_step = float(scn.get("light_spacing_m", 60.0))
    pole_step = float(ff.get("lightpole_spacing_m", 55.0))
    gantry_step = float(ff.get("gantry_spacing_m", 320.0))
    npalm = ntree = npost = npole = ngantry = 0
    PNC, PNR = 2, 2  # palms atlas 2x2

    for cls, pts, half in edges:
        is_fw = cls in FREEWAY
        if is_fw and want_guardrails:
            guardrails.append(_guardrail(pts, half=half))         # left shoulder
            guardrails.append(_guardrail_other(pts, half))        # right shoulder
        # walk the edge placing props at intervals
        acc_v = acc_l = acc_p = acc_g = 0.0
        for i in range(1, len(pts)):
            seg = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][2] - pts[i - 1][2])
            acc_v += seg; acc_l += seg; acc_p += seg; acc_g += seg
            x, y, z = pts[i]
            a, b = pts[i - 1], pts[min(len(pts) - 1, i + 1)]
            tx, tz = b[0] - a[0], b[2] - a[2]; tl = math.hypot(tx, tz) or 1.0
            nx, nz = -tz / tl, tx / tl
            # SRP-style overhead gantry spanning the freeway every gantry_step
            if is_fw and want_gantries and acc_g >= gantry_step:
                acc_g = 0.0
                if not on_road_tight(x, z) and not in_pad(x, z):   # not spanning a crossing road at a junction
                    gm, gs = _gantry(x, y, z, tx / tl, tz / tl, half)
                    gantry_metal.append(gm); gantry_sign.append(gs); ngantry += 1
            # streetlight posts on surface streets; SRP poles on freeways. Furniture sits right at the
            # shoulder by design, so it is NOT run through the vegetation corridor rejecter (which would
            # reject everything within half+margin of the road — i.e. exactly where furniture belongs).
            if is_fw and acc_p >= pole_step:
                acc_p = 0.0
                ox, oz = x + nx * (half + 2.5), z + nz * (half + 2.5)
                if not on_road_tight(ox, oz) and not in_pad(ox, oz):
                    poles.append(_place_obj(pole_V, pole_T, ox, y, oz, yaw=math.atan2(tz, tx))); npole += 1
            elif want_streetlights and not is_fw and acc_l >= light_step:
                acc_l = 0.0
                ox, oz = x - nx * (half + 1.8), z - nz * (half + 1.8)
                if not on_road_tight(ox, oz) and not in_pad(ox, oz):    # keep the streetlight off crossing roads + tracks
                    posts.append(_post(ox, y, oz)); npost += 1
            # low SoCal shrubs/chaparral on ALL verges (incl. freeway cuts) — sparse, off the road
            if acc_v >= 13.0:
                for side in (1.0, -1.0):
                    if rnd.random() > 0.40:
                        continue
                    off = half + 2.5 + rnd.random() * 11.0
                    bx, bz = x + nx * off * side, z + nz * off * side
                    if on_road(bx, bz) or in_pad(bx, bz):
                        continue
                    gy = ground_at(bx, bz) - 0.25
                    sz = 1.1 + rnd.random() * 1.8
                    bushes.append(_billboard_cell(bx, gy, bz, rnd.randint(0, 1), rnd.randint(0, 1),
                                                  2, 2, sz * 0.8, sz, rnd.random() * math.pi)); nbush += 1
            # vegetation on surface streets only (freeways get furniture, not trees)
            if not is_fw:
                veg_step = 24.0 if cls in ("primary", "secondary") else 34.0
                if acc_v >= veg_step:
                    acc_v = 0.0
                    for side in (1.0, -1.0):
                        if rnd.random() > 0.62:
                            continue
                        off = half + 3.5 + rnd.random() * 9.0
                        px, pz = x + nx * off * side, z + nz * off * side
                        if on_road(px, pz) or in_pad(px, pz):
                            continue
                        gy = ground_at(px, pz) - 0.3
                        if palms_enabled and rnd.random() < 0.5:   # palm
                            h = 8.5 + rnd.random() * 6.0
                            palms.append(_billboard_cell(px, gy, pz, rnd.randint(0, PNC - 1), rnd.randint(0, PNR - 1),
                                                         PNC, PNR, h, h * 0.42, rnd.random() * math.pi)); npalm += 1
                        else:                    # broadleaf street tree
                            h = 5.5 + rnd.random() * 3.5
                            trees.append(_billboard_cell(px, gy, pz, rnd.randint(0, 1), rnd.randint(0, 1),
                                                         2, 2, h, h * 0.9, rnd.random() * math.pi)); ntree += 1

    # Lake Murray water. NOTE _water_disc(cx, cz, y) — the disc is centred (cx,cz) in X-Z at height y. The
    # arg ORDER matters: pass (x, z, height), not (x, height, z), or the disc's Z-position becomes its
    # altitude (this was the K10 reservoir floating at 23 km).
    lk = scn.get("lake_murray", {})
    waters = []
    if "center_lon" in lk and lk.get("enabled", True):
        lx, ly, lz = to_local(lk["center_lon"], lk["center_lat"], 0.0)
        wy = y_at(lk["center_lon"], lk["center_lat"]) - 1.0
        waters.append(_water_disc(lx, lz, wy, float(lk.get("radius_m", 600))))

    # Named water bodies (real reservoirs/lakes along the corridor) — flat discs at terrain level, one WATER
    # group (CSP [Material_Water]). Gated by scenery.water_bodies.enabled: the K10 inter-track reservoirs are
    # fictional dressing over compressed positions, so they are OFF by default there.
    nwater = 0
    if (scn.get("water_bodies", {}) or {}).get("enabled", True):
        for wb in (scn.get("water_bodies", {}) or {}).get("bodies", []):
            wx, _wy, wz = to_local(wb["center_lon"], wb["center_lat"], 0.0)
            if in_pad(wx, wz):                      # no reservoir inside a race track footprint
                continue
            wy = y_at(wb["center_lon"], wb["center_lat"]) - 1.0
            waters.append(_water_disc(wx, wz, wy, float(wb.get("radius_m", 150))))
            nwater += 1

    # Pacific ocean backdrop: a big flat sea-level quad out to the WEST (the coast is ~1 km past the
    # map edge). Double-sided. CSP water shader + aerial haze sell it as the distant ocean.
    oc = scn.get("ocean", {})
    if oc.get("enabled"):
        all_lat = [pt[1] for f in fc["features"] for pt in f["geometry"]["coordinates"]]
        lat_s, lat_n = min(all_lat), max(all_lat)
        pad = float(oc.get("lat_pad_m", 4000)) / 111_000.0
        sea = float(oc.get("sea_level_m", 0.0))
        lon_a, lon_b = float(oc["lon_from"]), float(oc["lon_to"])
        corners = [(lon_a, lat_s - pad), (lon_b, lat_s - pad), (lon_b, lat_n + pad), (lon_a, lat_n + pad)]
        V = [to_local(lo, la, sea) for lo, la in corners]
        # seat the ocean a touch below sea-level datum so the coastline DEM meets it cleanly
        V = [(x, y - 1.0, z) for x, y, z in V]
        waters.append({"vertices": V, "uvs": [(0, 0), (1, 0), (1, 1), (0, 1)],
                       "tris": _ds([(0, 1, 2), (0, 2, 3)])})

    # Front Range backdrop: hazy layered ridges far to the true WEST. Placed by REAL longitude
    # (dlon<0) through the mirror-aware projector so it lands at true west in-game; after the sun-yaw
    # fix (true_north_rotation_deg) the sun sets behind it. Flat DOUBLE-SIDED silhouettes; CSP aerial
    # haze blurs them. Config-driven (scenery.mountains). MUST stay OFF the GrassFX occluder list.
    mtn_cfg = scn.get("mountains", {})
    mountains = None
    if mtn_cfg.get("enabled", False):
        import random as _mrnd
        _mr = _mrnd.Random(int(mtn_cfg.get("seed", 9)))
        layers = mtn_cfg.get("layers", [{"dlon": -0.20, "base_h_m": 720.0, "amp_m": 320.0},
                                        {"dlon": -0.28, "base_h_m": 1120.0, "amp_m": 440.0}])
        span = float(mtn_cfg.get("lat_span_deg", 0.34)); ncol = max(4, int(mtn_cfg.get("columns", 140)))
        # base_y_m seats the ridge skirt near the ground (not a -200 m pit); max_h_m hard-caps the silhouette
        # so the range reads as a faint FAR backdrop, not a wall towering over the tracks.
        base_y = float(mtn_cfg.get("base_y_m", 20.0)); max_h = float(mtn_cfg.get("max_h_m", 500.0))
        mnt_v, mnt_t = [], []
        for layer, lay in enumerate(layers):
            wlon = lon0 + float(lay["dlon"]); base_h = float(lay["base_h_m"]); amp = float(lay["amp_m"])
            x = sx * (wlon - lon0) * m_lon                # mirror-aware -> true west
            prev = None
            for i in range(ncol):
                lat = lat0 - span / 2 + span * i / (ncol - 1)
                z = (lat - lat0) * m_lat
                h = base_h + amp * (0.5 * math.sin(i * 0.4 + layer) + 0.3 * math.sin(i * 1.3 + layer * 2)
                                    + 0.2 * (2 * _mr.random() - 1))
                h = min(max_h, max(base_h * 0.5, h))
                b = len(mnt_v)
                mnt_v.append((x, base_y, z)); mnt_v.append((x, h, z))
                if prev is not None:
                    p0, p1 = prev
                    mnt_t += [(p0, p1, b + 1), (p0, b + 1, b), (p0, b + 1, p1), (p0, b, b + 1)]  # 2-sided
                prev = (b, b + 1)
        mountains = {"vertices": mnt_v, "uvs": [(0.0, 0.0)] * len(mnt_v), "tris": mnt_t}

    # --- buildings from OSM footprints (ported from build_env.py): extruded boxes with façade variety,
    #     kept NEAR the corridor and off the road, capped, double-sided (network convention). ---
    from collections import defaultdict as _dd

    from scripts.environment import buildings as _bld
    bcfg = scn.get("buildings", {}) or {}
    COMMERCIAL = ["BUILDINGS", "BRICK", "STUCCO"]        # tilt-up concrete / brick / stucco
    WAREHOUSES = ["WAREHOUSE", "WHMETAL"]                # weathered concrete + corrugated metal
    bwalls = {g: [] for g in COMMERCIAL + WAREHOUSES}
    broof = {"ROOFS": [], "RFMETAL": []}
    nbld = 0
    bpath = data / "buildings.geojson"
    if bcfg.get("enabled", True) and bpath.exists():
        bset = json.loads(bpath.read_text()).get("buildings", [])
        near = float(bcfg.get("near_lap_m", 300)); minA = float(bcfg.get("min_area_m2", 35))
        offr = float(bcfg.get("off_road_m", 16)); whA = float(bcfg.get("warehouse_area_m2", 700))
        cap = int(bcfg.get("cap", 2500))
        RCELL = 40.0
        rbuckets = _dd(list)
        for _cls, pts, _h in edges:
            for x, _y, z in pts:
                rbuckets[(int(x // RCELL), int(z // RCELL))].append((x, z))

        def _dist_road(cx, cz, rad):
            ci, cj = int(cx // RCELL), int(cz // RCELL); rr = int(rad // RCELL) + 1; best = 1e18
            for di in range(-rr, rr + 1):
                for dj in range(-rr, rr + 1):
                    for px, pz in rbuckets.get((ci + di, cj + dj), ()):
                        best = min(best, (cx - px) ** 2 + (cz - pz) ** 2)
            return best ** 0.5

        def _area(f):
            a = 0.0
            for i in range(len(f)):
                x0, z0 = f[i]; x1, z1 = f[(i + 1) % len(f)]; a += x0 * z1 - x1 * z0
            return abs(a) * 0.5

        for b in bset:
            if nbld >= cap:
                break
            foot = [(to_local(lo, la, 0.0)[0], to_local(lo, la, 0.0)[2]) for lo, la in b["coords"]]
            if len(foot) < 3:
                continue
            A = _area(foot)
            if A < minA:
                continue
            cx = sum(p[0] for p in foot) / len(foot); cz = sum(p[1] for p in foot) / len(foot)
            if in_pad(cx, cz):                                        # never inside a race track footprint
                continue
            if _dist_road(cx, cz, near) > near:                       # keep near-corridor only
                continue
            if any(_dist_road(fx, fz, offr) < offr for fx, fz in foot):  # never ON the road
                continue
            bury = 2.5
            base = min(y_at(lo, la) for lo, la in b["coords"]) - bury
            box = _bld.extrude(foot, base, float(b.get("height_m") or 6.5) + bury)
            box["walls"]["tris"] = _ds(box["walls"]["tris"]); box["roof"]["tris"] = _ds(box["roof"]["tris"])
            if A > whA:
                g = WAREHOUSES[int(abs(round(cx) * 5 + round(cz) * 11)) % len(WAREHOUSES)]
                bwalls[g].append(box["walls"]); broof["RFMETAL" if g == "WHMETAL" else "ROOFS"].append(box["roof"])
            else:
                g = COMMERCIAL[int(abs(round(cx) * 7 + round(cz) * 13)) % len(COMMERCIAL)]
                bwalls[g].append(box["walls"]); broof["ROOFS"].append(box["roof"])
            nbld += 1

    groups = []
    groups += _pack("GUARDRAIL", "GUARDRAIL", guardrails)
    groups += _pack("GANTRY", "GANTRY", gantry_metal)
    groups += _pack("FWSIGN", "FWSIGN", gantry_sign)
    groups += _pack("LIGHTS_pole", "LIGHTS", poles)
    groups += _pack("LIGHTS_post", "LIGHTS", posts)
    groups += _pack("PALMS", "PALMS", palms)
    groups += _pack("TREES", "TREES", trees)
    groups += _pack("BUSHES", "BUSHES", bushes)
    for g, ms in bwalls.items():
        if ms:
            groups += _pack(g, g, ms)
    for g, ms in broof.items():
        if ms:
            groups += _pack(g, g, ms)
    if waters:
        groups.append(("WATER", "WATER", _merge(waters)))
    if mountains:
        groups.append(("MOUNTAINS", "MOUNTAINS", mountains))

    nv, nf = write_obj(data / "environment.obj", "environment.mtl", groups)
    _write_mtl(data / "environment.mtl")
    stats = {"guardrail_groups": sum(1 for g in groups if g[0].startswith("GUARDRAIL")),
             "gantries": ngantry, "srp_poles": npole, "streetlights": npost,
             "palms": npalm, "trees": ntree, "bushes": nbush, "buildings": nbld,
             "water_bodies": nwater, "lake": bool(waters), "mountains": bool(mountains),
             "env_vertices": nv, "env_tris": nf}
    print("network env:", json.dumps(stats))
    return stats


def _guardrail_other(pts, half):
    """Guardrail on the opposite (right) side of the edge."""
    V, U, T = [], [], []
    arc = 0.0; rows = []
    for i in range(len(pts)):
        if i > 0:
            arc += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][2] - pts[i - 1][2])
        x, y, z = pts[i]
        a = pts[max(0, i - 1)]; b = pts[min(len(pts) - 1, i + 1)]
        tx, tz = b[0] - a[0], b[2] - a[2]; tl = math.hypot(tx, tz) or 1.0
        nx, nz = -tz / tl, tx / tl
        px, pz = x - nx * (half + 0.5), z - nz * (half + 0.5)
        r = len(V)
        V.append((px, y + 0.45, pz)); U.append((arc / 4.0, 0.0))
        V.append((px, y + 0.45 + 0.85, pz)); U.append((arc / 4.0, 1.0))
        rows.append(r)
    for k in range(len(rows) - 1):
        a0, a1, b0, b1 = rows[k], rows[k] + 1, rows[k + 1], rows[k + 1] + 1
        T += [(a0, a1, b1), (a0, b1, b0)]
    return {"vertices": V, "uvs": U, "tris": _ds(T)}


def _post(x, y, z, h=9.0, r=0.18):
    V = [(x - r, y, z), (x + r, y, z), (x + r, y + h, z), (x - r, y + h, z),
         (x, y, z - r), (x, y, z + r), (x, y + h, z + r), (x, y + h, z - r)]
    return {"vertices": V, "uvs": [(0.0, 0.0)] * 8, "tris": _ds([(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)])}


def _corridor(edges, *, margin, cell=20.0):
    from collections import defaultdict
    buckets = defaultdict(list)
    for _cls, pts, half in edges:
        for x, _y, z in pts:
            buckets[(int(x // cell), int(z // cell))].append((x, z, half))

    def on_road(x, z):
        ci, cj = int(x // cell), int(z // cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for px, pz, hw in buckets.get((ci + di, cj + dj), ()):
                    if math.hypot(x - px, z - pz) < hw + margin:
                        return True
        return False
    return on_road


def _write_mtl(path: Path) -> None:
    path.write_text("newmtl GUARDRAIL\nKd 0.74 0.75 0.77\n\nnewmtl GANTRY\nKd 0.66 0.68 0.70\n\n"
                    "newmtl FWSIGN\nKd 0.06 0.30 0.16\n\nnewmtl LIGHTS\nKd 0.30 0.30 0.32\n\n"
                    "newmtl PALMS\nKd 0.20 0.34 0.14\n\nnewmtl TREES\nKd 0.13 0.30 0.11\n\n"
                    "newmtl BUSHES\nKd 0.30 0.34 0.18\n\nnewmtl WATER\nKd 0.05 0.20 0.34\n\n"
                    "newmtl MOUNTAINS\nKd 0.55 0.60 0.70\n\n"
                    "newmtl BUILDINGS\nKd 0.62 0.62 0.60\n\nnewmtl BRICK\nKd 0.50 0.32 0.26\n\n"
                    "newmtl STUCCO\nKd 0.78 0.72 0.60\n\nnewmtl WAREHOUSE\nKd 0.58 0.58 0.55\n\n"
                    "newmtl WHMETAL\nKd 0.66 0.68 0.70\n\nnewmtl ROOFS\nKd 0.28 0.28 0.30\n\n"
                    "newmtl RFMETAL\nKd 0.60 0.60 0.62\n", encoding="utf-8")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "projects/san-diego-cruise")
