"""Export a tiny per-layout SPAWN kn5 for each layout that has its own start point.

The combined "K10 - San Diego" kn5 holds BOTH the Lake Murray loop and the freeway network as ONE shared
geometry kn5, kept free of AC_START/AC_PIT dummies. To give each layout (`full` = loop, `freeway`) its own
spawn, we ship a small per-layout kn5 that contains ONLY that layout's dummies; models_<layout>.ini loads
both (main geometry + this spawn stub). Standard AC "layouts share one kn5, spawns in a per-config kn5".

Dummies come from data/dummies_<layout>.json (an `{name:[x,y,z], ..., "_facing_yaw":rad}` map — loop from
build_kn5, freeway from merge_freeway). Empties are placed with the SAME local->Blender remap the combined
geometry uses: (x,y,z) local (E,up,N) -> Blender (x, z, y). AC_START/PIT/HOTLAP face `_facing_yaw`.

    "$BLENDER" --background --python scripts/ac/build_spawn_kn5.py -- <project-dir>
"""

import json
import sys
import tempfile
import zipfile
from pathlib import Path

import addon_utils
import bpy

FACING = ("AC_START", "AC_PIT", "AC_HOTLAP")


def _project_dir(argv):
    if "--" not in argv:
        raise SystemExit("usage: blender --background --python build_spawn_kn5.py -- <project-dir>")
    rest = argv[argv.index("--") + 1:]
    if not rest:
        raise SystemExit("missing <project-dir> after '--'")
    return Path(rest[0]).resolve()


project = _project_dir(sys.argv)
repo = Path(__file__).resolve().parents[2]
cfg = json.loads((project / "track.config.json").read_text())
slug = cfg["slug"]
data = project / "data"
build = project / "build"
build.mkdir(parents=True, exist_ok=True)

# install + enable the vendored AC add-on (zipped on the fly)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon_src = repo / "vendor" / "io_import_accsv"
if not (addon_src / "__init__.py").exists():
    raise SystemExit(f"vendored AC add-on missing at {addon_src}")
_zip = Path(tempfile.gettempdir()) / "_ac_addon_spawn.zip"
with zipfile.ZipFile(_zip, "w", zipfile.ZIP_DEFLATED) as z:
    for f in addon_src.rglob("*"):
        if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts:
            z.write(f, Path("io_import_accsv") / f.relative_to(addon_src))
bpy.ops.preferences.addon_install(filepath=str(_zip), overwrite=True)
for mod in addon_utils.modules():
    if any(k in mod.__name__.lower() for k in ("assetto", "corsa", "accsv")):
        try:
            bpy.ops.preferences.addon_enable(module=mod.__name__)
        except Exception as e:  # noqa: BLE001
            print("enable err", e)


def _find_export_op():
    if hasattr(bpy.ops, "exporter") and hasattr(bpy.ops.exporter, "kn5"):
        return bpy.ops.exporter.kn5
    for modname in dir(bpy.ops):
        mod = getattr(bpy.ops, modname)
        for opname in dir(mod):
            if "kn5" in opname.lower() or "assetto" in opname.lower():
                return getattr(mod, opname)
    return None


op = _find_export_op()
# Clear any leftover persistence .ini from the main export (it lists every embedded texture -> would bloat
# a dummies-only spawn kn5 by tens of MB of unused textures).
for old in build.glob("*.ini"):
    old.unlink()

built = []
for lo in cfg.get("layouts", []):
    lid = lo.get("id")
    if not lo.get("spawn"):
        continue
    dpath = data / f"dummies_{lid}.json"
    if not dpath.exists():
        print(f"[spawn_kn5] no dummies_{lid}.json — skip {lid}")
        continue
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    dummies = json.loads(dpath.read_text())
    yaw = float(dummies.pop("_facing_yaw", 0.0))
    n = 0
    for name, pos in dummies.items():
        if not (isinstance(pos, (list, tuple)) and len(pos) == 3):
            continue
        x, y, z = pos
        e = bpy.data.objects.new(name, None)
        e.empty_display_type = "ARROWS"; e.empty_display_size = 4.0
        e.location = (x, z, y)                                  # local (E,up,N) -> Blender (E,N,up)
        if any(name.startswith(p) for p in FACING):
            e.rotation_euler = (0.0, 0.0, yaw)
        bpy.context.scene.collection.objects.link(e)
        n += 1
    out = build / f"{slug}__{lid}.kn5"
    try:
        out.unlink()
    except OSError:
        pass
    for ob in bpy.data.objects:
        ob.select_set(True)
    if op is not None:
        try:
            op("EXEC_DEFAULT", filepath=str(out))
        except Exception as ex:  # noqa: BLE001
            import traceback
            print(f"[spawn_kn5] export EXCEPTION {lid}: {ex}")
            traceback.print_exc()
    exists = out.exists()
    print(f"[spawn_kn5] {lid}: kn5={exists} size={out.stat().st_size if exists else 0} dummies={n}")
    if exists:
        built.append(lid)
print(f"[spawn_kn5] built spawn kn5s for: {built}")
