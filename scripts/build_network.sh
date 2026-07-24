#!/usr/bin/env bash
# End-to-end Mac build for a FREEROAM NETWORK track (osm-network source) — now backed by the
# CENTRAL ENGINE: every stage that exists in prodrive-ac-builder runs from .engine/scripts/ at the
# tag pinned in .engine-version (./bootstrap.sh fetches it). SD keeps only what is SD-unique:
# the freeway-grade audit gate (H_grade_launch lives in scripts/geometry/audit_mesh.py) and the
# network Blender program (scripts/ac/build_network_kn5.py).
#
#   ./scripts/build_network.sh project_freeway_net            # full run
#   ./scripts/build_network.sh project_freeway_net mesh       # resume from the mesh stage
#   ./scripts/build_network.sh <project-dir> --list           # list stages and exit
#
# Stages: net (Overpass) -> classify (OSM class/lanes/layer) -> elev (3DEP) -> mesh (+audit) -> env
#         -> blend (Blender 4.2) -> kn5 (addon export) -> verify -> fidelity -> pack (folder + zip).
#
# NOTE: this is the NETWORK pipeline (branching graph, ramps, viaducts). The Lake Murray loop keeps
# its own path: loop.blend is the truth there and ships via scripts/build_kn5.sh.
set -euo pipefail

PROJ="${1:?usage: build_network.sh <project-dir> [from-stage|--list]}"
FROM="${2:-net}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
STAGES=(net classify elev mesh env blend kn5 pack)

# central engine (prodrive-ac-builder) pinned by .engine-version, fetched into gitignored .engine/
[[ -d "$ROOT/.engine/scripts" ]] || "$ROOT/bootstrap.sh"
ENGINE="$ROOT/.engine"
# engine stages need the project dir ABSOLUTE (they run with cwd=.engine)
PROJ_ABS="$(cd "$PROJ" && pwd)"
eng() { local label="$1"; shift; echo; echo "━━━━ [$label] (engine) $*"; (cd "$ENGINE" && "$@"); }

if [[ "$FROM" == "--list" ]]; then printf '%s\n' "${STAGES[@]}"; exit 0; fi
START=-1; for i in "${!STAGES[@]}"; do [[ "${STAGES[$i]}" == "$FROM" ]] && START=$i; done
[[ $START -lt 0 ]] && { echo "unknown stage '$FROM' (one of: ${STAGES[*]})" >&2; exit 1; }
run() { echo; echo "━━━━ [$1] ${*:2}"; "${@:2}"; }
active() { local s; for s in "${STAGES[@]:$START}"; do [[ "$s" == "$1" ]] && return 0; done; return 1; }

SLUG="$($PY -c "import json;print(json.load(open('$PROJ/track.config.json'))['slug'])")"
VER="$($PY -c "import json;print(json.load(open('$PROJ/track.config.json'))['version'])")"

active net      && eng net      "$PY" -m scripts.gps.network "$PROJ_ABS"
active classify && eng classify "$PY" -m scripts.gps.classify "$PROJ_ABS"
active elev     && eng elev     "$PY" -m scripts.elevation.network_elev "$PROJ_ABS"
active mesh     && eng mesh     "$PY" -m scripts.geometry.build_network_mesh "$PROJ_ABS"   # runs the ENGINE mesh-audit at its end
# Hard gate: the audit must be CLEAN on the drivability defects (supports-in-road, terrain-poke)
# before we spend Blender time. Junction crossings (C) are reported but don't block (walls are
# gapped there). This runs SD's LOCAL audit_mesh — it carries the SD-unique H_grade_launch check
# (sustained freeway deck grade = launch ramp) that the engine's audit does not have yet; the
# engine's own audit already ran inside the mesh stage above, so both gates apply.
active mesh     && run audit    "$PY" - "$PROJ" <<'PY'
import sys, json
from scripts.geometry.audit_mesh import audit
r = audit(sys.argv[1])
if r["A_supports_in_road"] or r["B_terrain_poke"] or r["H_grade_launch"]:
    print("✗ audit gate FAILED (supports-in-road / terrain-poke / launch-grade) — not building the kn5"); sys.exit(1)
print(f"✓ audit gate passed (A={r['A_supports_in_road']} B={r['B_terrain_poke']} C={r['C_junction_crossings']} H={r['H_grade_launch']})")
PY
active env      && eng env      "$PY" -m scripts.environment.build_network_env "$PROJ_ABS"

if active blend || active kn5; then
  BLENDER="${BLENDER:-$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender}"
  # blend stays SD-local: build_network_kn5.py is SD-unique (the engine's network path assembles
  # with its OBJ build_kn5.py; SD's program carries the loop-frame conventions the combined build needs)
  active blend && run blend "$BLENDER" --background --python scripts/ac/build_network_kn5.py -- "$PROJ_ABS"
  active kn5   && run kn5   "$BLENDER" --background --python "$ENGINE/scripts/ac/export_kn5_addon.py" -- "$PROJ_ABS"
  active kn5   && eng verify "$PY" -m scripts.ac.verify_kn5 "$PROJ_ABS"
  # Fidelity gate on the SHIPPED kn5 vs the audited OBJs — engine kn5_ground_check (its generic
  # 1ROAD bucket sees this network's 1ROAD_part* groups since v0.16.0).
  active kn5   && eng fidelity "$PY" -m scripts.ac.kn5_ground_check "$PROJ_ABS"
fi

if active pack; then
  eng pack "$PY" -m scripts.ac.track_folder "$PROJ_ABS"
  cp -f "$PROJ/build/$SLUG.kn5" "$PROJ/build/$SLUG/$SLUG.kn5"
  run pack "$PY" - "$PROJ" "$SLUG" "$VER" <<'PY'
import sys, zipfile
from pathlib import Path
proj, slug, ver = sys.argv[1:4]
root = Path(proj) / "build"; folder = root / slug
zp = root / f"{slug}_v{ver}.zip"
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, 6) as z:
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.suffix != ".svg":
            z.write(f, f.relative_to(root))
print(f"  -> {zp}  ({zp.stat().st_size/1e6:.1f} MB)")
PY
fi
echo; echo "✓ network build complete: $PROJ ($SLUG v$VER)"
