"""Prep loop.blend for AC and save blender/<slug>.blend (then export_kn5_addon exports the kn5).

Blender-first: loop.blend is the source of truth (Kevin shapes it). This pass consumes it, renames the
working meshes to AC surface conventions, gives them tiling UVs + PBR materials, welds/holes-fills the
grass, drops the AC_START/PIT/TIME/HOTLAP dummies computed from the real centreline, packs textures, and
saves blender/<slug>.blend. export_kn5_addon then binds shaders + exports the real kn5.

    blender --background --python scripts/ac/build_kn5.py -- <project-dir>
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy  # provided by Blender


# working mesh name (in loop.blend) -> AC-convention name. `1` prefix = physical surface keyed to
# surfaces.ini (ROAD/KERB/GRASS); 1WALL = physical collision barrier (guardrail).
AC_NAME = {
    "ROAD": "1ROAD_road",
    "KERB": "1KERB_kerb",
    "TERRAIN": "1GRASS",
    "GUARDRAIL": "1WALL_guard",
}
UV_TILE = 4.0  # metres per texture tile (top-down planar projection)


def _project_dir(argv):
    if "--" not in argv:
        raise SystemExit("usage: blender --background --python build_kn5.py -- <project-dir>")
    rest = argv[argv.index("--") + 1:]
    if not rest:
        raise SystemExit("missing <project-dir>")
    return Path(rest[0]).resolve()


def planar_uv(me):
    """Top-down planar UVs (u=x/tile, v=y/tile) — tiles asphalt/grass/kerb consistently at world scale."""
    uv = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    for loop in me.loops:
        co = me.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x / UV_TILE, co.y / UV_TILE)


def main():
    proj = _project_dir(sys.argv)
    repo = proj.parent
    sys.path.insert(0, str(repo))
    cfg = json.loads((proj / "track.config.json").read_text())
    slug = cfg["slug"]
    (proj / "blender").mkdir(exist_ok=True)
    (proj / "build").mkdir(exist_ok=True)

    # 1. open the working scene
    bpy.ops.wm.open_mainfile(filepath=str(proj / "loop.blend"))

    # 2. drop non-exported helpers (markers/cameras), rename meshes to AC convention
    for ob in list(bpy.data.objects):
        if ob.type != "MESH":
            bpy.data.objects.remove(ob, do_unlink=True); continue
        ob.name = AC_NAME.get(ob.name, ob.name)

    import bmesh
    for ob in [o for o in bpy.data.objects if o.type == "MESH"]:
        me = ob.data
        planar_uv(me)
        bm = bmesh.new(); bm.from_mesh(me)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        if ob.name.upper().startswith("1GRASS"):
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.05)   # terrain must be watertight
            try:
                bmesh.ops.holes_fill(bm, edges=bm.edges, sides=16)
            except Exception as e:
                print(f"[build_kn5] holes_fill skipped: {e}")
        # DRIVABLE surfaces must face UP or AC has no top collision and the car falls through. The
        # local->Blender axis remap (x,y,z)->(x,z,y) is a reflection that flipped every face down. Fix
        # DETERMINISTICALLY: reverse only the faces whose normal points down (normal.z<0). This is NOT
        # recalc_face_normals (which guesses "outward" from shape and picks DOWN on big terrain); for a
        # near-horizontal drivable surface "up" is unambiguous, so this can't guess wrong or split the mesh.
        if ob.name.upper().startswith(("1ROAD", "1GRASS", "1KERB")):
            bm.normal_update()
            down = [f for f in bm.faces if f.normal.z < 0]
            if down:
                bmesh.ops.reverse_faces(bm, faces=down)
            print(f"[build_kn5] {ob.name}: flipped {len(down)}/{len(bm.faces)} faces up")
        bm.to_mesh(me); bm.free(); me.update()
        if len(me.loops) > 65535:            # smooth-shade big meshes so render-verts fit the 16-bit cap
            for poly in me.polygons:
                poly.use_smooth = True
            me.update()
        if len(me.vertices) > 65535:
            print(f"[build_kn5] WARNING: {ob.name} has {len(me.vertices)} verts (>65535)")

    # 3. AC dummies from the real centreline (local frame x=E,y=up,z=N -> Blender x=E,y=N,z=up = (x,z,y))
    from scripts.geometry import dummies as dmod
    local = json.loads((proj / "data" / "centerline.local.json").read_text())
    cl = [tuple(p) for p in local["points_xyz_m"]]
    widths = local["widths_m"]
    layouts = cfg.get("layouts", [{}])
    n_pits = 8
    placed = dmod.place_dummies(cl, widths, n_sectors=3, n_pits=n_pits)
    # start facing = travel direction pts[0]->pts[1], as a yaw about Blender Z(up)
    tx, ty = cl[1][0] - cl[0][0], cl[1][2] - cl[0][2]          # Blender (E, N)
    start_yaw = math.atan2(tx, ty)
    FACING = ("AC_START", "AC_PIT", "AC_HOTLAP")
    dummies_out = {}
    for name, (x, y, z) in placed.items():
        e = bpy.data.objects.new(name, None)
        e.empty_display_type = "ARROWS"; e.empty_display_size = 4.0
        e.location = (x, z, y)                                  # local (E,up,N) -> Blender (E,N,up)
        if any(name.startswith(p) for p in FACING):
            e.rotation_euler = (0.0, 0.0, start_yaw)
        bpy.context.scene.collection.objects.link(e)
        dummies_out[name] = [round(x, 3), round(y, 3), round(z, 3)]
    (proj / "data" / "dummies.json").write_text(json.dumps(dummies_out, indent=1))

    # 4. PBR materials (prefix -> texture set); export_kn5_addon re-derives shader from the same prefix
    from scripts.ac import pbr
    for ob in [o for o in bpy.data.objects if o.type == "MESH"]:
        pbr.setup_material(bpy, ob)

    # 5. pack textures + save the AC-ready .blend for the export pass
    try:
        bpy.ops.file.pack_all()
    except Exception as e:
        print(f"[build_kn5] pack_all warning: {e}")
    out = proj / "blender" / f"{slug}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    meshes = [o.name for o in bpy.data.objects if o.type == "MESH"]
    print(f"[build_kn5] prepped {meshes}  + {len(dummies_out)} dummies -> {out}")


main()
