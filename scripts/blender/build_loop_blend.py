"""Build the Lake Murray main loop as EDITABLE Blender meshes — the starting point for live work.

This is deliberately NOT a kn5 exporter. It drops the real-road, real-elevation loop into a .blend so
Kevin (designer) and Claude (operator) can shape it interactively in Blender before any AC export. What it
creates, at REAL sampled elevation (metres, Blender Z-up, +Y = north):

  ROAD    — the drivable ribbon (1ROAD), swept ±width/2 along the centreline, tight to real elevation
  KERB    — corner kerbs, sitting right on the road edge (kept tight to the road, not floating)
  TERRAIN — the grass ground, conformed to the road and CLAMPED to ±terrain_band_m of the nearest road
            so the raw DEM's Cowles-area peaks can't tower over a neighbourhood loop with none

Run headless:
  <blender> --background --python scripts/blender/build_loop_blend.py -- project
Then open project/loop.blend in the Blender GUI to work on it live.
"""

import ast
import json
import math
import struct
import sys
from pathlib import Path

import bpy  # noqa: provided by Blender


# --- minimal .npy reader + grid projection (copied so the importer stands alone) ------------------

def read_npy(path):
    with open(path, "rb") as f:
        assert f.read(6) == b"\x93NUMPY"
        f.read(2)
        hlen = struct.unpack("<H", f.read(2))[0]
        header = ast.literal_eval(f.read(hlen).decode())
        ny, nx = header["shape"]
        data = struct.unpack(f"<{ny * nx}d", f.read(8 * ny * nx))
    return [list(data[j * nx:(j + 1) * nx]) for j in range(ny)]


def meters_per_degree(lat0):
    m_lat = 111_132.92 - 559.82 * math.cos(2 * math.radians(lat0))
    m_lon = 111_412.84 * math.cos(math.radians(lat0)) - 93.5 * math.cos(3 * math.radians(lat0))
    return m_lon, m_lat


def project_grid(grid, meta, lon0, lat0, elev0):
    s, w, n, e = meta["bbox_swne"]
    nx, ny, sp = meta["nx"], meta["ny"], meta["spacing_m"]
    midlat = (s + n) / 2
    gy = sp / 111_000.0
    gx = sp / (111_000.0 * math.cos(math.radians(midlat)))
    m_lon, m_lat = meters_per_degree(lat0)
    out = []
    for j in range(ny):
        lat = n - j * gy
        out.append([((w + i * gx - lon0) * m_lon, grid[j][i] - elev0, (lat - lat0) * m_lat)
                    for i in range(nx)])
    return out


def carve_road_corridor(grid_xyz, centerline, widths, *, carve=0.2, iters=12):
    """GUARANTEE the terrain never crests over the road. conform+clearance grade grid NODES, but a coarse
    facet spanning a sunk node and a higher one just outside can still cross over the thin ribbon (worst
    at the I-8 bridge cut, where the deck is raised over the freeway embankment). Here we sample the road
    ribbon directly and, wherever the interpolated terrain sits at/above the road, push that grid cell's
    four corners to road-height minus ``carve`` — iterating until no sample pokes. Mutates in place."""
    ny = len(grid_xyz); nx = len(grid_xyz[0])
    x0 = grid_xyz[0][0][0]; z0 = grid_xyz[0][0][2]
    dx = grid_xyz[0][1][0] - x0; dz = z0 - grid_xyz[1][0][2]   # rows run north->south (z decreases with j)

    def cell(tx, tz):
        fi = (tx - x0) / dx; fj = (z0 - tz) / dz
        return max(0, min(nx - 2, int(fi))), max(0, min(ny - 2, int(fj))), fi, fj

    def th(tx, tz):
        i0, j0, fi, fj = cell(tx, tz); ti = fi - i0; tj = fj - j0
        a = grid_xyz[j0][i0][1]; b = grid_xyz[j0][i0 + 1][1]
        c = grid_xyz[j0 + 1][i0][1]; d = grid_xyz[j0 + 1][i0 + 1][1]
        return (a * (1 - ti) + b * ti) * (1 - tj) + (c * (1 - ti) + d * ti) * tj

    def perp(i):
        a = centerline[max(0, i - 1)]; b = centerline[min(len(centerline) - 1, i + 1)]
        ex, ez = b[0] - a[0], b[2] - a[2]; L = math.hypot(ex, ez) or 1.0
        return (-ez / L, ex / L)

    offs = [k / 10 * 1.08 for k in range(-10, 11)]        # across the ribbon + a touch past both edges
    for _ in range(iters):
        changed = False
        for i, (x, y, z) in enumerate(centerline):
            px, pz = perp(i); hw = widths[i] / 2
            for o in offs:
                tx, tz = x + px * o * hw, z + pz * o * hw
                if th(tx, tz) > y - 0.005:
                    i0, j0, _, _ = cell(tx, tz); lim = y - carve
                    for jj in (j0, j0 + 1):
                        for ii in (i0, i0 + 1):
                            g = grid_xyz[jj][ii]
                            if g[1] > lim:
                                grid_xyz[jj][ii] = (g[0], lim, g[2]); changed = True
        if not changed:
            break


def band_clamp(grid_xyz, road_pts, band):
    """Clamp every terrain cell to within ±band of the nearest road point's height (kills fake peaks)."""
    from collections import defaultdict
    B = 130.0
    buck = defaultdict(list)
    for (x, y, z) in road_pts:
        buck[(int(x // B), int(z // B))].append((x, y, z))

    def nearest_y(x, z):
        bx, bz = int(x // B), int(z // B)
        best_y, best_d, r = None, 1e18, 1
        while best_y is None and r < 40:
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if r > 1 and max(abs(dx), abs(dz)) != r:
                        continue
                    for (rx, ry, rz) in buck.get((bx + dx, bz + dz), ()):
                        d = (x - rx) ** 2 + (z - rz) ** 2
                        if d < best_d:
                            best_d, best_y = d, ry
            r += 1
        return best_y

    for j in range(len(grid_xyz)):
        for i in range(len(grid_xyz[0])):
            x, y, z = grid_xyz[j][i]
            ry = nearest_y(x, z)
            if ry is not None:
                grid_xyz[j][i] = (x, max(ry - band, min(ry + band, y)), z)


# --- Blender mesh creation ------------------------------------------------------------------------

def _load_i8(proj):
    p = proj / "data" / "i8.local.json"
    return [tuple(x) for x in json.loads(p.read_text())] if p.exists() else []


def _i8_nearest(i8, x, z):
    """Nearest distance from (x,z) to the I-8 polyline + the interpolated I-8 floor height there."""
    bd, floor = 1e18, None
    for k in range(len(i8) - 1):
        ax, ay, az = i8[k]; bx, by, bz = i8[k + 1]
        dx, dz = bx - ax, bz - az; L2 = dx * dx + dz * dz
        if L2 == 0:
            continue
        t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / L2))
        cx, cz = ax + t * dx, az + t * dz
        dd = math.hypot(x - cx, z - cz)
        if dd < bd:
            bd = dd; floor = ay + (by - ay) * t
    return bd, floor


def cut_i8_trench(grid_xyz, i8, *, half_w=16.0, bank=6.0, depth=1.0):
    """Sink the terrain in the I-8 corridor down to the freeway's real grade so the loop bridges span a
    real cut. Within ``half_w`` of the I-8 centreline drop to floor-``depth``; ramp back to the existing
    ground over ``bank``. Runs AFTER carve_road_corridor so the cut wins under the crossing decks. All
    non-crossing loop roads sit >22 m from I-8, so a half_w+bank<=22 m cut never undercuts the frontage."""
    if not i8:
        return
    for j in range(len(grid_xyz)):
        for i in range(len(grid_xyz[0])):
            x, y, z = grid_xyz[j][i]
            bd, floor = _i8_nearest(i8, x, z)
            if floor is None:
                continue
            if bd <= half_w:
                target = floor - depth
            elif bd < half_w + bank:
                tt = (bd - half_w) / bank
                target = (floor - depth) * (1 - tt) + y * tt
            else:
                continue
            if y > target:
                grid_xyz[j][i] = (x, target, z)


def build_guardrails(centerline, widths, i8, *, h=0.95, over=22.0, pad=3):
    """Vertical guardrail panels along BOTH road edges wherever the loop is over the I-8 cut (perp dist to
    I-8 < ``over``). Extends ``pad`` points onto each approach so the rail starts before the gap."""
    verts, tris = [], []
    if not i8:
        return {"vertices": verts, "uvs": [], "tris": tris}
    on = [(_i8_nearest(i8, p[0], p[2])[0] < over) for p in centerline]
    # dilate the over-mask by pad so rails run onto the approaches
    mask = [any(on[max(0, k - pad):k + pad + 1]) for k in range(len(on))]
    def perp(i):
        a = centerline[max(0, i - 1)]; b = centerline[min(len(centerline) - 1, i + 1)]
        ex, ez = b[0] - a[0], b[2] - a[2]; L = math.hypot(ex, ez) or 1.0
        return (-ez / L, ex / L)
    runs, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j + 1 < len(mask) and mask[j + 1]:
                j += 1
            runs.append((i, j)); i = j + 1
        else:
            i += 1
    for a, b in runs:
        for side in (1, -1):
            base = len(verts)
            for k in range(a, b + 1):
                x, y, z = centerline[k]; px, pz = perp(k); hw = widths[k] / 2
                ex, ez = x + px * side * hw, z + pz * side * hw
                verts.append((ex, y, ez))        # bottom (deck level)
                verts.append((ex, y + h, ez))    # top
            for m in range(b - a):
                v = base + m * 2
                # double-sided quad (two tris each winding) so it reads from either side
                tris.append((v, v + 1, v + 3)); tris.append((v, v + 3, v + 2))
                tris.append((v, v + 3, v + 1)); tris.append((v, v + 2, v + 3))
    return {"vertices": verts, "uvs": [], "tris": tris}


def make_mesh(name, mesh_dict, rgba):
    """mesh_dict has 'vertices' [(x,y,z)] in local (x=E, y=up, z=N); remap to Blender Z-up (x, z, y)."""
    verts = [(vx, vz, vy) for (vx, vy, vz) in mesh_dict["vertices"]]
    faces = [tuple(t) for t in mesh_dict["tris"]]
    if not verts or not faces:
        print(f"[blend] {name}: empty, skipped")
        return None
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = False
    mat.diffuse_color = rgba
    me.materials.append(mat)
    print(f"[blend] {name}: {len(verts)} verts, {len(faces)} faces")
    return ob


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["project"]
    proj = Path(argv[0]).resolve()
    repo = proj.parent
    sys.path.insert(0, str(repo))
    from scripts.geometry import ribbon, kerbs

    data = proj / "data"
    local = json.loads((data / "centerline.local.json").read_text())
    centerline = [tuple(p) for p in local["points_xyz_m"]]
    widths = local["widths_m"]
    lon0, lat0 = local["origin"]["lon"], local["origin"]["lat"]
    elev0 = local["origin"]["elev_m"]

    cfg = json.loads((proj / "track.config.json").read_text())
    band = float(cfg.get("terrain", {}).get("terrain_band_m", 22.0))

    # --- road + kerbs (tight to real elevation) ---
    road = ribbon.road_ribbon(centerline, widths)
    road["vertices"] = [(x, y + 0.10, z) for (x, y, z) in road["vertices"]]   # ~0.1 m proud of the terrain
    kerb = kerbs.corner_kerbs(centerline, widths)
    kerb["vertices"] = [(x, y + 0.12, z) for (x, y, z) in kerb["vertices"]]

    # --- terrain: upsample the coarse 40 m DEM (finer facets), conform to the road with a small
    #     clearance so near-road ground sits just BELOW the road edge (no coarse facet pokes up through
    #     and buries the ribbon), then clamp to ±band so there are no fake mountains ---
    grid = read_npy(data / "heightfield.npy")
    meta = json.loads((data / "heightfield.meta.json").read_text())
    grid, meta = ribbon.upsample_grid(grid, meta, 2)                      # 40 m -> 20 m facets
    grid_xyz = project_grid(grid, meta, lon0, lat0, elev0)
    ribbon.conform_terrain_to_road(grid_xyz, centerline, widths, corridor=20.0, blend=16.0, clearance=0.30)
    band_clamp(grid_xyz, centerline, band)
    carve_road_corridor(grid_xyz, centerline, widths, carve=0.2)   # guarantee no ground pokes through
    i8 = _load_i8(proj)
    cut_i8_trench(grid_xyz, i8)                                     # sink the I-8 corridor into a real cut
    terrain = ribbon.grass_terrain(grid_xyz)
    guardrail = build_guardrails(centerline, widths, i8)           # rails where the loop bridges I-8

    # --- fresh scene ---
    bpy.ops.wm.read_factory_settings(use_empty=True)
    make_mesh("TERRAIN", terrain, (0.30, 0.42, 0.20, 1.0))
    make_mesh("ROAD", road, (0.09, 0.09, 0.10, 1.0))
    make_mesh("KERB", kerb, (0.75, 0.16, 0.16, 1.0))
    make_mesh("GUARDRAIL", guardrail, (0.82, 0.82, 0.86, 1.0))

    ys = [p[1] for p in centerline]
    print(f"[blend] loop: {len(centerline)} pts, road elevation {min(ys):.0f}..{max(ys):.0f} m (rel origin {elev0:.0f} m)")
    out = proj / "loop.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print(f"[blend] wrote {out}")


main()
