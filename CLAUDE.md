# k10-san-diego — Lake Murray Loop

A standalone repo for **one** Assetto Corsa track: a drivable loop around **Lake Murray**, San Diego
(Kevin's childhood neighborhood). Split out of the general `prodrive-ac-builder` so it can grow its own
way, with a different working method.

---

## The central engine (prodrive-ac-builder)

This repo **consumes the shared track engine** instead of carrying hand-ported copies of it (the copies
went stale twice — that's why the consolidation happened):

- `./bootstrap.sh` clones/fetches `prodrive-ac-builder` into git-ignored **`.engine/`** at the tag
  pinned in **`.engine-version`**. Run it after clone and after bumping the pin.
- Every build stage the engine covers runs **from `.engine/`**:
  `(cd .engine && python3 -m scripts.<pkg>.<mod> <ABSOLUTE-project-dir>)` — the wrappers
  (`scripts/build_network.sh`, `scripts/build_kn5.sh`, `scripts/build_combined.sh`) do this already.
- `scripts/` here keeps **only what is SD-unique**: the network Blender assembler
  (`ac/build_network_kn5.py`), freeway-merge/combine tooling, the loop's routing + shaping helpers,
  the freeway launch-grade audit (`geometry/audit_mesh.py` check H), and the Blender live tools.
  Since the **v0.16.0** pin the engine also carries the Blender-first loop front-end
  (`.engine/scripts/ac/build_kn5_loop.py` — the port of SD's old `ac/build_kn5.py`), so the last
  local engine-copies (pbr, export addon, ground check, track_folder, ext_config, verify,
  install, credits) are **gone** — all those stages run from `.engine/`.

---

## The method (this is the important part)

> **Kevin is the designer. Claude is the Blender operator. We build it *live in Blender together*,
> shaping the real geometry by hand, and only export to kn5 once it's right.**

This is a deliberate pivot away from the old fully-headless "config → kn5 in one shot" pipeline. That
pipeline was good at *mapping the real world* but bad at *judgement calls* — it kept guessing wrong about
which roads, how the terrain should read, where things belong. So we're inverting it:

1. **Map the real loop** from OpenStreetMap + USGS elevation (headless, deterministic — the part the
   pipeline is good at). This is already done; the result is in `project/data/`.
2. **Import it into Blender** as editable meshes at real elevation (`scripts/blender/build_loop_blend.py`).
3. **Work on it live.** Kevin directs ("raise this crest", "tighten that kerb", "the bridge should…"),
   Claude operates Blender. The `.blend` is the source of truth from here on.
4. **Export to kn5 later**, once the geometry is right. (Not built yet — added when we get there.)

When operating Blender, **show, don't assert**: check the actual mesh/scene state before claiming
something; when Kevin says the real world is a certain way and the data disagrees, trust Kevin and fix the
data — he knows this neighborhood.

## Where we're starting

**Just the separated main loop** — nothing else. Five real roads, stitched into one closed ring:

```
Alvarado Rd → College Ave → Navajo Rd → Jackson Drive → Lake Murray Blvd / 70th → (close)
```

13.5 km, encircling the reservoir. No connectors, no buildings, no plants, no scenery. That all gets
added later, deliberately, in Blender.

---

## Lessons carried over (do NOT relearn these the hard way)

These were paid for in the old repo. They are baked into the data and the scripts here; keep them.

- **Roads and kerbs stay TIGHT to their real sampled elevation.** The road is the ground truth. Kerbs sit
  right on the road edge, not floating above or sunk below it.
- **Map the REAL roads.** The loop is routed along the actual OSM drivable network
  (`scripts/gps/street_route.py`), not traced or guessed. Divided roads pick one carriageway; a road whose
  name changes through an interchange (70th ⇄ Lake Murray Blvd over I-8) still connects on real pavement.
  Never invent a road (there is **no** "Cowles Mountain road" — that was a stitching error).
- **No fake mountains.** The raw USGS DEM carries real peaks (Cowles ~500 m) that tower over a
  neighborhood loop with none. The terrain is clamped to within **±`terrain.terrain_band_m`** (22 m) of
  the nearest road height, so distant peaks are cut to the road grade. *Roads keep their real elevation*
  — Jackson/Navajo genuinely climb the San Carlos hills (~213 m), Alvarado sits low by I-8 (~108 m).
- **Bridges are held LEVEL.** Bare-earth DEMs (3DEP) contain no bridge decks, so a road crossing a freeway
  samples the trench underneath and dips. `scripts/geometry/bridge_level.py` holds each real OSM bridge
  deck (College Ave, Lake Murray Blvd) level between its abutments. Run it AFTER projection, BEFORE mesh.
- **Interchange aliasing.** Where a flat road threads a freeway interchange, a coarse terrain grid can
  alias the road up into the embankment (70th St read a 57% cliff). Fix: sample that road's elevation
  directly from 3DEP at its own points, not the grid.
- **Units & axes.** Everything metric. The local frame is `x = east, y = up (elevation), z = north`
  (AC/kn5 is Y-up). The Blender importer remaps to Blender **Z-up** (`x, z, y`), +Y = north.

## Repo layout

```
k10-san-diego/
├── CLAUDE.md                     # this file
├── project/
│   ├── track.config.json         # the one track's source of truth (roads, bbox, terrain knobs)
│   ├── loop.blend                # the live Blender scene — WORK HERE
│   ├── data/                     # the mapped loop (derived, deterministic)
│   │   ├── centerline.geojson        # loop as lon/lat (real roads)
│   │   ├── centerline.elevation.json # along-lap real elevation profile
│   │   ├── centerline.local.json     # loop projected to local metres + per-vertex widths  ← Blender reads this
│   │   ├── heightfield.npy / .meta.json  # terrain grid (real DEM)
│   │   ├── network.cache.json        # cached OSM drivable network (so re-derivation needs no live Overpass)
│   │   └── bridges.cache.json        # cached OSM bridge spans
│   └── source/                   # notes / screenshots
├── project_freeway/              # freeway LAYOUT, single-ribbon centerline build (the older approach)
├── project_freeway_net/          # freeway as the REAL NETWORK (all ramps) — network-pipeline project
│   ├── track.config.json         # osm-network source: I-5/CA-52/CA-163/I-8 box, downtown to 32.705
│   ├── source/realworld_capture.json  # texture_overrides: CC0 cement on 1ROAD/HWYSTRUCT/1WALL etc.
│   └── data/                     # network.geojson + elevation + heightfield committed (offline rebuild);
│                                 # track.obj/environment.obj are regenerated, git-ignored
├── bootstrap.sh / .engine-version   # central-engine pin: fetches prodrive-ac-builder into .engine/
├── .engine/                      # the pinned engine checkout (git-ignored; run ./bootstrap.sh)
└── scripts/                      # SD-UNIQUE code only — engine-covered stages run from .engine/
    ├── config.py
    ├── gps/          # centerline, street_route (real-network routing), overpass, kml, road_widths
    ├── trace/osm.py
    ├── elevation/    # heightfield, usgs_3dep (loop-path libraries; network 3DEP runs from .engine)
    ├── geometry/     # ribbon (road/kerb/terrain), kerbs, projection, bridge_level, dummies, palm,
    │                 # scenery, smooth, extra_lines, build_mesh+build_network_mesh (audit libraries),
    │                 # audit_mesh (SD-unique H launch-grade gate; engine audit runs in the mesh stage)
    ├── environment/  # (network scatter/furniture/water runs from .engine)
    ├── blender/      # build_loop_blend.py — imports the loop into Blender; live/ operator tools
    └── ac/           # SD-unique only: build_network_kn5 (network Blender assembler),
                      # merge_freeway, combine_layouts. Loop prep/export/verify/ground/pack all
                      # run from .engine/ (v0.16.0+ — see scripts/build_kn5.sh)
vendor/io_import_accsv   # the jwl-7 AC Blender Tools addon, vendored (kn5 exporter; Blender 4.2 only)
```

## The freeway NETWORK pipeline (ported from prodrive-ac-builder, 2026-07-20)

The Lake Murray loop stays Blender-first (above). The **freeway** is different: it's a branching graph
(both carriageways + every on/off ramp and interchange connector), so it uses the deterministic network
pipeline ported from prodrive-ac-builder — `project_freeway_net/` is its project:

```bash
./scripts/build_network.sh project_freeway_net          # full run (Overpass -> ... -> zip)
./scripts/build_network.sh project_freeway_net mesh     # offline resume from cached data (usual case)
```
Stages: `net` (Overpass motorway+motorway_link, mainlines ref-filtered so I-805 stays out) → `classify`
→ `elev` (3DEP) → `mesh` (build_network_mesh: ribbons per edge, OSM-layer flyover decks, viaduct piers
with clear-span suppression, concrete walls, median fill) → **audit gate** (`audit_mesh`: A supports-in-
road and B terrain-poke must be 0 or the build stops) → `env` → `blend`/`kn5`/`pack` (Blender 4.2).
Piers are suppressed using the audit's own band (y_top−45 … y_top−1.5) with a ±3-cell scan — the fix
that cleared 6 downtown piers standing in I-5. `project_freeway/` (single-ribbon layout) is the retired
predecessor — the shipped track uses the network freeway, merged into the loop (below).

## The ONE combined "K10 - San Diego" kn5 (loop + freeway, `scripts/build_combined.sh`)

The shipped track is a SINGLE kn5 containing both the Lake Murray loop AND the freeway network, at their
real relative position (~12 km apart — "connect them eventually"). Build it with one command:
```bash
scripts/build_combined.sh           # merge the current freeway build into the loop -> combined kn5 + zip
scripts/build_combined.sh freeway   # rebuild the freeway (mesh+env, unmirrored) first, then merge
```
How the merge works — the loop stays Blender-first, the freeway is a build INPUT:
- The freeway network MUST be built `mirror_x=false` (matching the loop's frame). `scripts/ac/merge_freeway.py`
  translates its `track.obj` + `environment.obj` into the loop's local frame (origin delta + elevation-datum
  delta), suffixing group names `_fw` (keeping the 1ROAD_/1GRASS_/1WALL_/HWYSTRUCT prefix).
- The loop's prep pass (engine `build_kn5_loop.py`) imports those OBJs with the SAME `(x,z,y)`-reflect remap `make_mesh` uses, so
  they land in the loop frame and the existing rename/face-up-flip/PBR pass handles them. Walls + HWYSTRUCT
  are double-sided (the reflection flips their winding; a barrier must collide from both sides).
- One layout (`full`) = the whole thing. Ships as one zip via GitHub Releases.

**Lake Murray extras** (both feed the loop via `scripts/geometry/extra_lines.py` → `connectors.local.json`):
the **Del Cerro sub-loop** (Del Cerro Blvd/Ave, Airoso, Adelante, Colorado, Connecticut, Wisconsin —
`route.connectors`) and **per-direction carriageways** on divided roads (`route.split_carriageways`,
e.g. Navajo/College): the opposing carriageway is pulled from OSM, sampled at its OWN real 3DEP, and added
beside the loop where the two are cleanly divided; the main loop is narrowed to one carriageway there. Each
extra line carries real point-precise 3DEP (the sub-loop climbs the Del Cerro hill; College keeps its real
~6 m per-direction split).

## kn5 export (installable AC track)

`loop.blend` is the source of truth; the kn5 pipeline **consumes** it — so hand-edits ship. One command:
```bash
scripts/build_kn5.sh project     # -> project/build/<slug>_track.zip (drop into Content Manager)
```
Stages (all from the **central engine**, `.engine/` at the pinned tag): `build_kn5_loop.py` opens
loop.blend, renames the working meshes to AC conventions (ROAD→`1ROAD_road`, TERRAIN→`1GRASS`,
KERB→`1KERB_kerb`, GUARDRAIL→`1WALL_guard`), adds tiling UVs + PBR materials + the
AC_START/PIT/TIME/HOTLAP dummies, welds the grass watertight, and **flips drivable faces up** (the
local→Blender axis remap is a reflection that inverts winding — face-down = car falls through; fix
deterministically by reversing only normal.z<0 faces, never `recalc_face_normals`). Its **over-cap
guard is FATAL** — every mesh must land under 65,535 verts, so `build_loop_blend` pre-chunks the
clustered foliage (PALMFROND etc.) with `palm.chunk_mesh` (balanced median bisection; the engine's
equal-width band split alone once left a PALMFROND band at 82,797 verts). Then the engine
`export_kn5_addon.py` runs the vendored addon (Blender **4.2** — not 5) to write the kn5,
`verify_kn5` gates it (no dup meshes, drivable face-up, under the 65 k cap, spawns on road),
`kn5_ground_check` runs kn5-internal (Blender-first: no source OBJs), and `track_folder` writes
surfaces.ini / ui / map / models.ini / CSP ext_config. The loop's green-turf `1GRASS` look rides on
`texture_overrides` in `project/track.config.json` (the engine default is dry chaparral). Ships via
GitHub Releases, not git.

## Commands

**Build the Blender scene from the mapped loop** (resets base geometry — run when you want a fresh start):
```bash
BL="$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender"
"$BL" --background --python scripts/blender/build_loop_blend.py -- project
```
Then open `project/loop.blend` in the Blender GUI. **Once we start hand-editing, that `.blend` is the
truth — don't regenerate it without meaning to.**

**Re-map the loop from real roads** (only if the route/roads change) — offline, from the cached network:
```bash
python3 - <<'PY'
import json
from scripts.gps import overpass, centerline
from scripts.trace import osm
from collections import defaultdict
cache = json.load(open('project/data/network.cache.json'))
byname = defaultdict(list)
for w in cache:
    if w.get('name'): byname[w['name']].append([(x[0], x[1]) for x in w['geom']])
overpass.fetch_ways   = lambda bbox, names, **k: {n: byname.get(n, []) for n in names}
osm.fetch_drivable    = lambda bbox, **k: [{"geom": [(x[0], x[1]) for x in w['geom']]} for w in cache]
print(centerline.build_centerline('project')[1])
PY
# then: elevation (heightfield) → projection → bridge_level → rebuild the .blend
```

## Build environment

- **Mac** (this machine): all geometry + Blender work. Blender **4.2.9** at
  `$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app`. System `python3` (stdlib only — no numpy needed;
  the `.npy` reader is hand-rolled).
- **Windows**: only for the eventual in-game drive/QA once we're exporting kn5.
