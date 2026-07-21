"""Render a SLOW chase-cam flythrough of a freeroam NETWORK track (no single centerline).

Rides one freeway edge's real deck (with the OSM-layer flyover lift baked in, exactly like the mesh),
so you see the road surface, the physical walls, the shoulder/desert, and the car rising onto a stacked
interchange flyover. Deliberately slow — tune ``speed`` (m/s) and ``seconds``.

Run from Blender:
  blender --background --python scripts/ac/flythrough_network.py -- <project-dir> \
      [seconds] [speed_mps] [height_m] [edge_id]

Writes <project-dir>/build/_flynet/f_####.png (encode to mp4 with ffmpeg afterwards).
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # pbr.py (same dir)
import pbr  # noqa: E402


def _args():
    a = sys.argv
    rest = a[a.index("--") + 1:] if "--" in a else []
    pd = Path(rest[0])
    seconds = float(rest[1]) if len(rest) > 1 else 42.0
    speed = float(rest[2]) if len(rest) > 2 else 24.0      # m/s ground speed — SLOW by default
    height = float(rest[3]) if len(rest) > 3 else 26.0      # chase-cam metres above the deck
    edge_id = int(rest[4]) if len(rest) > 4 else -1         # -1 = auto (longest edge with a flyover)
    return pd, seconds, speed, height, edge_id


# --- deck reconstruction (inlined from build_network_mesh so it matches the exported geometry) --------
def _smooth_series(vals, win):
    n = len(vals)
    if n < 3 or win < 2:
        return list(vals)
    half = win // 2
    return [sum(vals[max(0, i - half):min(n, i + half + 1)]) /
            (min(n, i + half + 1) - max(0, i - half)) for i in range(n)]


def _layer_lift(pts, layer_profile, layer_h=5.5, smooth_win=28):
    n = len(pts)
    if not layer_profile or len(layer_profile) != n:
        return [0.0] * n
    raw = [max(0, int(l)) * layer_h for l in layer_profile]
    return _smooth_series(raw, smooth_win) if any(raw) else [0.0] * n


def _grade_cap_y(pts, max_grade=0.065):
    out = list(pts)
    for _ in range(2):
        for i in range(1, len(out)):
            cap = max_grade * max(0.5, math.hypot(out[i][0] - out[i - 1][0], out[i][2] - out[i - 1][2]))
            out[i] = (out[i][0], out[i - 1][1] + max(-cap, min(cap, out[i][1] - out[i - 1][1])), out[i][2])
        for i in range(len(out) - 2, -1, -1):
            cap = max_grade * max(0.5, math.hypot(out[i + 1][0] - out[i][0], out[i + 1][2] - out[i][2]))
            out[i] = (out[i][0], out[i + 1][1] + max(-cap, min(cap, out[i][1] - out[i + 1][1])), out[i][2])
    return out


def _mpd(lat0):
    phi = math.radians(lat0)
    m_lat = 111132.954 - 559.822 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi) + 0.118 * math.cos(5 * phi)
    return m_lon, m_lat


def _look_rot(eye, target):
    import mathutils
    fwd = (target - eye)
    fwd = fwd.normalized() if fwd.length > 1e-6 else mathutils.Vector((0, -1, 0))
    up = mathutils.Vector((0, 0, 1))
    right = fwd.cross(up)
    right = right.normalized() if right.length > 1e-6 else mathutils.Vector((1, 0, 0))
    tup = right.cross(fwd).normalized()
    return mathutils.Matrix((right, tup, -fwd)).transposed().to_euler()


def main():
    import bpy
    import mathutils
    pd, seconds, speed, height, edge_id = _args()
    fps = 20
    frames = max(2, int(seconds * fps))
    look_back, look_ahead, look_up = 12.0, 42.0, 1.2

    bpy.ops.wm.read_factory_settings(use_empty=True)
    for obj_file in ("track.obj", "environment.obj"):
        p = pd / "data" / obj_file
        if p.exists():
            bpy.ops.wm.obj_import(filepath=str(p), up_axis="Y", forward_axis="NEGATIVE_Z")
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        pbr.setup_material(bpy, o)

    # --- pick the edge + rebuild its deck (layer flyover lift, grade-capped) in local metres ---
    data = pd / "data"
    fc = json.loads((data / "network.geojson").read_text())["features"]
    elev = {e["id"]: e["z_smooth_m"] for e in json.loads((data / "network.elevation.json").read_text())["edges"]}
    loc = json.loads((data / "network.local.json").read_text())["origin"]
    lon0, lat0, elev0 = loc["lon"], loc["lat"], loc["elev_m"]
    sx = -1.0 if json.loads((data / "network.local.json").read_text()).get("mirror_x", True) else 1.0
    m_lon, m_lat = _mpd(lat0)

    def to_local(lon, lat, z):
        return (sx * (lon - lon0) * m_lon, z - elev0, (lat - lat0) * m_lat)

    by_id = {f["properties"]["id"]: f for f in fc}
    if edge_id < 0:   # auto: mainline edge with the highest flyover, tie-break longest
        best = None
        for f in fc:
            p = f["properties"]
            if p.get("is_ramp") or p.get("road_class") not in ("motorway", "trunk"):
                continue
            lp = p.get("layer_profile")
            key = (max(lp) if lp else 0, p["length_m"])
            if best is None or key > best[0]:
                best = (key, p["id"])
        edge_id = best[1]
    f = by_id[edge_id]
    c = f["geometry"]["coordinates"]
    z = elev.get(edge_id) or [0.0] * len(c)
    if len(z) != len(c):
        z = (z + [z[-1]] * len(c))[:len(c)]
    pts = [to_local(lo, la, zz) for (lo, la), zz in zip(c, z)]
    lay = _layer_lift(pts, f["properties"].get("layer_profile"))
    deck = _grade_cap_y([(x, y + lay[i], zz) for i, (x, y, zz) in enumerate(pts)]) if max(lay) > 1 else pts
    deck = [(x, y + 0.12, z2) for x, y, z2 in deck]     # +ROAD_LIFT_M: sit on the road surface
    print(f"[flynet] edge {edge_id} ({f['properties'].get('name')}) pts={len(deck)} maxlift={round(max(lay),1)}")

    # arc-length param (horizontal), centred window at the flyover peak
    arc = [0.0]
    for i in range(1, len(deck)):
        arc.append(arc[-1] + math.hypot(deck[i][0] - deck[i - 1][0], deck[i][2] - deck[i - 1][2]))
    total = arc[-1]
    peak_i = max(range(len(lay)), key=lambda i: lay[i]) if max(lay) > 1 else len(deck) // 2
    win = min(total, speed * seconds)
    c0 = max(win / 2, min(total - win / 2, arc[peak_i]))
    s_start = c0 - win / 2

    def sample(s):
        s = max(0.0, min(total, s))
        lo, hi = 0, len(arc) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if arc[mid] < s:
                lo = mid + 1
            else:
                hi = mid
        i = max(1, lo)
        t = (s - arc[i - 1]) / max(1e-6, arc[i] - arc[i - 1])
        a, b = deck[i - 1], deck[i]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)

    def to_bl(p):
        return mathutils.Vector((p[0], -p[2], p[1]))

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = 30
    cam_data.clip_end = 60000
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    for fr in range(frames):
        s = s_start + (fr / (frames - 1)) * win
        cam.location = to_bl(sample(s - look_back)) + mathutils.Vector((0, 0, height))
        target = to_bl(sample(s + look_ahead)) + mathutils.Vector((0, 0, look_up))
        cam.rotation_euler = _look_rot(cam.location, target)
        cam.keyframe_insert("location", frame=fr + 1)
        cam.keyframe_insert("rotation_euler", frame=fr + 1)

    # --- sun + Nishita sky (desert daylight) ---
    sun_data = bpy.data.lights.new("Sun", "SUN"); sun_data.energy = 2.4
    sun = bpy.data.objects.new("Sun", sun_data)
    bpy.context.scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(54), math.radians(16), math.radians(40))
    world = bpy.data.worlds.new("Sky"); world.use_nodes = True
    bpy.context.scene.world = world
    sky = world.node_tree.nodes.new("ShaderNodeTexSky")
    try:
        sky.sky_type = "NISHITA"
    except Exception:
        pass
    bg = world.node_tree.nodes.get("Background")
    world.node_tree.links.new(sky.outputs[0], bg.inputs[0]); bg.inputs[1].default_value = 0.5

    sc = bpy.context.scene
    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    try:
        sc.eevee.taa_render_samples = 12
    except Exception:
        pass
    sc.view_settings.view_transform = "Standard"; sc.view_settings.exposure = -0.6
    sc.render.resolution_x, sc.render.resolution_y = 1280, 720
    sc.frame_start, sc.frame_end = 1, frames
    sc.render.fps = fps
    sc.render.image_settings.file_format = "PNG"
    fdir = pd / "build" / "_flynet"
    fdir.mkdir(parents=True, exist_ok=True)
    for old in fdir.glob("f_*.png"):
        old.unlink()
    sc.render.filepath = str(fdir / "f_")
    bpy.ops.render.render(animation=True)
    print("FLYNET_DONE", fdir, frames, fps)


main()
