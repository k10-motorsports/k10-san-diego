#!/usr/bin/env bash
# End-to-end Mac build for a FREEROAM NETWORK track (osm-network source) — ported from
# prodrive-ac-builder. Runs every phase from track.config.json to an installable .zip, and runs the
# mesh-audit at the end of the generative pass (support-in-road / terrain-poke / junction-crossing).
#
#   ./scripts/build_network.sh project_freeway_net            # full run
#   ./scripts/build_network.sh project_freeway_net mesh       # resume from the mesh stage
#   ./scripts/build_network.sh <project-dir> --list           # list stages and exit
#
# Stages: net (Overpass) -> classify (OSM class/lanes/layer) -> elev (3DEP) -> mesh (+audit) -> env
#         -> blend (Blender 4.2) -> kn5 (addon export) -> verify -> pack (folder + zip).
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

if [[ "$FROM" == "--list" ]]; then printf '%s\n' "${STAGES[@]}"; exit 0; fi
START=-1; for i in "${!STAGES[@]}"; do [[ "${STAGES[$i]}" == "$FROM" ]] && START=$i; done
[[ $START -lt 0 ]] && { echo "unknown stage '$FROM' (one of: ${STAGES[*]})" >&2; exit 1; }
run() { echo; echo "━━━━ [$1] ${*:2}"; "${@:2}"; }
active() { local s; for s in "${STAGES[@]:$START}"; do [[ "$s" == "$1" ]] && return 0; done; return 1; }

SLUG="$($PY -c "import json;print(json.load(open('$PROJ/track.config.json'))['slug'])")"
VER="$($PY -c "import json;print(json.load(open('$PROJ/track.config.json'))['version'])")"

active net      && run net      "$PY" -m scripts.gps.network "$PROJ"
active classify && run classify "$PY" -m scripts.gps.classify "$PROJ"
active elev     && run elev     "$PY" -m scripts.elevation.network_elev "$PROJ"
active mesh     && run mesh     "$PY" -m scripts.geometry.build_network_mesh "$PROJ"    # runs mesh-audit at its end
# Hard gate: the audit must be CLEAN on the drivability defects (supports-in-road, terrain-poke) before we
# spend Blender time. Junction crossings (C) are reported but don't block (walls are gapped there).
active mesh     && run audit    "$PY" - "$PROJ" <<'PY'
import sys, json
from scripts.geometry.audit_mesh import audit
r = audit(sys.argv[1])
if r["A_supports_in_road"] or r["B_terrain_poke"]:
    print("✗ audit gate FAILED (supports-in-road or terrain-poke) — not building the kn5"); sys.exit(1)
print(f"✓ audit gate passed (A={r['A_supports_in_road']} B={r['B_terrain_poke']} C={r['C_junction_crossings']})")
PY
active env      && run env      "$PY" -m scripts.environment.build_network_env "$PROJ"

if active blend || active kn5; then
  BLENDER="${BLENDER:-$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender}"
  active blend && run blend "$BLENDER" --background --python scripts/ac/build_network_kn5.py -- "$PROJ"
  active kn5   && run kn5   "$BLENDER" --background --python scripts/ac/export_kn5_addon.py -- "$PROJ"
  active kn5   && run verify "$PY" -m scripts.ac.verify_kn5 "$PROJ"
fi

if active pack; then
  run pack "$PY" -m scripts.ac.track_folder "$PROJ"
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
