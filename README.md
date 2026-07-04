# k10-san-diego

A standalone Assetto Corsa track project: a drivable loop around **Lake Murray**, San Diego.

Built **Blender-first** — the real loop is mapped from OpenStreetMap at real USGS elevation, imported into
Blender as editable geometry, and shaped by hand before any kn5 export. See [CLAUDE.md](CLAUDE.md) for the
working method and the hard-won lessons (real elevation, real roads, no fake mountains, level bridges).

Starting point: the separated main loop — Alvarado Rd → College Ave → Navajo Rd → Jackson Drive →
Lake Murray Blvd/70th.

```bash
# build the Blender scene, then open project/loop.blend
BL="$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender"
"$BL" --background --python scripts/blender/build_loop_blend.py -- project
```
