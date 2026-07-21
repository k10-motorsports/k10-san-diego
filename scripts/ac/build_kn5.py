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
    # The Lake Murray street curb+sidewalk ships as the working mesh "CURB". Remap it to a 1KERB_ physical
    # surface so (a) it's collidable — the car can't fall through the road-edge/grass gap it bridges — and
    # (b) it gets the face-up flip below. Without this it stayed "CURB": non-physical AND textureless=black.
    "CURB": "1KERB_sidewalk",
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


def _split_large(ob, cap=60000):
    """Split a mesh over AC's 65,535-vertex cap into UNIQUELY-named spatial tiles (base_0, base_1, ...).
    The exporter otherwise auto-splits into DUPLICATE-named meshes, and AC drops dup-named PHYSICAL meshes
    from collision -> the car falls through (the 46 km freeway terrain is ~268k verts). Partitions faces
    into bands along the longest axis; pbr/surfaces still key off the shared name prefix (1GRASS_0 -> GRASS)."""
    me = ob.data
    if len(me.vertices) <= cap:
        return
    K = len(me.vertices) // cap + 1
    co = [(v.co.x, v.co.y, v.co.z) for v in me.vertices]
    ax = 0 if (max(c[0] for c in co) - min(c[0] for c in co)) >= \
             (max(c[1] for c in co) - min(c[1] for c in co)) else 1
    lo = min(c[ax] for c in co); span = (max(c[ax] for c in co) - lo) or 1.0
    bands = [[] for _ in range(K)]
    for p in me.polygons:
        bands[min(K - 1, int((p.center[ax] - lo) / span * K))].append(p)
    base = ob.name
    mat = me.materials[0] if me.materials else None
    # carry authored per-vertex UVs across the split (palm cards etc. are single UV per vertex) — without
    # this the tiles have NO UVs and an alpha-cutout texture samples (0,0) and vanishes.
    vert_uv = None
    if me.uv_layers.active:
        uvl = me.uv_layers.active
        vert_uv = [(0.0, 0.0)] * len(me.vertices)
        for loop in me.loops:
            vert_uv[loop.vertex_index] = tuple(uvl.data[loop.index].uv)
    for k, polys in enumerate(bands):
        if not polys:
            continue
        used, nv, nf, orig = {}, [], [], []
        for poly in polys:
            idx = []
            for vi in poly.vertices:
                if vi not in used:
                    used[vi] = len(nv); nv.append(co[vi]); orig.append(vi)
                idx.append(used[vi])
            nf.append(idx)
        nm = bpy.data.meshes.new(f"{base}_{k}")
        nm.from_pydata(nv, [], nf); nm.update()
        if vert_uv is not None:
            nuv = nm.uv_layers.new(name="UVMap")
            for loop in nm.loops:
                nuv.data[loop.index].uv = vert_uv[orig[loop.vertex_index]]
        if mat:
            nm.materials.append(mat)
        bpy.context.scene.collection.objects.link(bpy.data.objects.new(f"{base}_{k}", nm))
    print(f"[build_kn5] split {base} ({len(me.vertices)} verts) -> {K} tiles under {cap}")
    bpy.data.objects.remove(ob, do_unlink=True)


def planar_uv(me):
    """Top-down planar UVs (u=x/tile, v=y/tile) — tiles asphalt/grass/kerb consistently at world scale."""
    uv = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    for loop in me.loops:
        co = me.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x / UV_TILE, co.y / UV_TILE)


# freeway groups whose winding must not matter: after the loop's local->Blender reflection their faces
# would point inward, so double-side them (append reversed faces) — a barrier/structure must render AND
# collide from both sides. Drivable freeway surfaces are handled by the face-up flip pass instead.
DOUBLE_SIDE = ("1WALL", "HWYSTRUCT")


def _add_obj_meshes(obj_path):
    """Import a merged OBJ (the freeway, already translated to the loop frame) using the SAME local->Blender
    remap as build_loop_blend.make_mesh — (x,y,z)->(x,z,y) with reversed winding — so it lands in the loop's
    frame and the existing rename/flip/material pass handles it. Keeps AC group names + authored UVs."""
    verts = []; uvs = []; groups = []; cur = None
    for ln in obj_path.read_text().splitlines():
        s = ln.split()
        if not s:
            continue
        if s[0] == "v":
            verts.append((float(s[1]), float(s[2]), float(s[3])))
        elif s[0] == "vt":
            uvs.append((float(s[1]), float(s[2])))
        elif s[0] == "o":
            cur = (ln[2:].strip(), []); groups.append(cur)
        elif s[0] == "f" and cur is not None:
            face = []
            for tok in s[1:]:
                p = tok.split("/")
                face.append((int(p[0]) - 1, int(p[1]) - 1 if len(p) > 1 and p[1] else None))
            cur[1].append(face)
    n = 0
    for name, faces in groups:
        if not faces:
            continue
        has_uv = all(t is not None for fc in faces for _, t in fc)
        idx = {}; bverts = []; buvs = []; bfaces = []
        for fc in faces:
            bf = []
            for vi, ti in fc:
                if vi not in idx:
                    idx[vi] = len(bverts)
                    x, y, z = verts[vi]; bverts.append((x, z, y))     # local (E,up,N) -> Blender (E,N,up)
                    buvs.append(uvs[ti] if (has_uv and ti is not None) else (0.0, 0.0))
                bf.append(idx[vi])
            bfaces.append(tuple(reversed(bf)))                        # reverse winding (cancels the reflection)
        if name.upper().startswith(DOUBLE_SIDE):
            bfaces += [tuple(reversed(f)) for f in bfaces]            # render + collide from both sides
        me = bpy.data.meshes.new(name); me.from_pydata(bverts, [], bfaces); me.validate(); me.update()
        if has_uv:
            uvl = me.uv_layers.new(name="UVMap")
            for loop in me.loops:
                uvl.data[loop.index].uv = buvs[loop.vertex_index]
        bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, me))
        n += 1
    print(f"[build_kn5] merged {n} groups ({len(verts)} verts) from {obj_path.name}")
    return n


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

    # 1b. merge the freeway network (translated to the loop frame by scripts.ac.merge_freeway) into ONE kn5.
    #     Present only when the combined K10 - San Diego build has run merge_freeway; skipped otherwise.
    for mobj in sorted(list((proj / "data").glob("track_*.obj")) + list((proj / "data").glob("env_*.obj"))):
        _add_obj_meshes(mobj)

    # 2. drop non-exported helpers (markers/cameras), rename meshes to AC convention
    for ob in list(bpy.data.objects):
        if ob.type != "MESH":
            bpy.data.objects.remove(ob, do_unlink=True); continue
        ob.name = AC_NAME.get(ob.name, ob.name)

    # split any mesh over the AC per-mesh vertex cap into uniquely-named tiles (big tracks: freeway terrain)
    for ob in list(o for o in bpy.data.objects if o.type == "MESH"):
        _split_large(ob)

    import bmesh
    for ob in [o for o in bpy.data.objects if o.type == "MESH"]:
        me = ob.data
        # props / billboards carry authored UVs — don't flatten (freeway BUSHES/CHAINLINK too)
        if not ob.name.upper().startswith(("PALM", "TREE", "SCRUB", "BUSHES", "CHAINLINK", "ROADTEXT")):
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
        # ...plus flat painted markings (MARKINGS/YLINE) — decals that must face up to be seen from the car.
        if ob.name.upper().startswith(("1ROAD", "1GRASS", "1KERB", "MARKINGS", "YLINE")):
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
    # The main kn5 is kept SPAWN-FREE so several layouts can share it, each with its OWN start point.
    # The loop's dummies go to data/dummies_full.json; scripts/ac/build_spawn_kn5.py exports the tiny
    # <slug>__full.kn5 spawn stub (and __freeway.kn5 for the merged freeway layout) that models_<layout>.ini
    # loads alongside this geometry kn5. (Baking spawns here would collide with the freeway layout's spawn.)
    dummies_out = {name: [round(x, 3), round(y, 3), round(z, 3)] for name, (x, y, z) in placed.items()}
    dummies_out["_facing_yaw"] = round(start_yaw, 6)
    (proj / "data" / "dummies_full.json").write_text(json.dumps(dummies_out, indent=1))

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
