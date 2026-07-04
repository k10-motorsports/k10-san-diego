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

    # --- terrain: conform to road, then clamp to ±band so there are no fake mountains ---
    grid = read_npy(data / "heightfield.npy")
    meta = json.loads((data / "heightfield.meta.json").read_text())
    grid_xyz = project_grid(grid, meta, lon0, lat0, elev0)
    ribbon.conform_terrain_to_road(grid_xyz, centerline, widths)
    band_clamp(grid_xyz, centerline, band)
    terrain = ribbon.grass_terrain(grid_xyz)

    # --- fresh scene ---
    bpy.ops.wm.read_factory_settings(use_empty=True)
    make_mesh("TERRAIN", terrain, (0.30, 0.42, 0.20, 1.0))
    make_mesh("ROAD", road, (0.09, 0.09, 0.10, 1.0))
    make_mesh("KERB", kerb, (0.75, 0.16, 0.16, 1.0))

    ys = [p[1] for p in centerline]
    print(f"[blend] loop: {len(centerline)} pts, road elevation {min(ys):.0f}..{max(ys):.0f} m (rel origin {elev0:.0f} m)")
    out = proj / "loop.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print(f"[blend] wrote {out}")


main()
