#!/usr/bin/env bash
# Build the ONE combined "K10 - San Diego" kn5: the Lake Murray loop (with the Del Cerro sub-loop +
# per-direction carriageways) PLUS the freeway network, merged into a single track folder + installable zip.
#
# The freeway is built by its own network pipeline (scripts/build_network.sh project_freeway_net) and must
# be UNMIRRORED (mirror_x=false) to share the loop's frame. This script assumes both source builds are
# current; pass `freeway` as the first arg to rebuild the freeway mesh+env first.
#
#   scripts/build_combined.sh              # merge current freeway build into the loop -> combined kn5
#   scripts/build_combined.sh freeway      # rebuild the freeway (mesh+env) first, then merge
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
BL="${BLENDER:-$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender}"
PY="${PYTHON:-python3}"

if [[ "${1:-}" == "freeway" ]]; then
  echo "━━ rebuild freeway network (unmirrored)"
  "$PY" -m scripts.geometry.build_network_mesh project_freeway_net
  "$PY" -m scripts.geometry.audit_mesh          project_freeway_net
  "$PY" -m scripts.environment.build_network_env project_freeway_net
fi

echo "━━ refresh the loop's extra lines (Del Cerro sub-loop + split carriageways) + level bridges"
"$PY" -m scripts.geometry.extra_lines  project
"$PY" -m scripts.geometry.bridge_level project
echo "━━ rebuild loop.blend (loop + connectors + per-direction carriageways)"
"$BL" --background --python scripts/blender/build_loop_blend.py -- project | grep -iE "connector|narrow|loop:" || true

echo "━━ translate the freeway into the loop frame (merge_freeway)"
"$PY" -m scripts.ac.merge_freeway project project_freeway_net

echo "━━ build the ONE combined kn5 (loop imports the freeway) -> verify -> folder -> zip"
exec scripts/build_kn5.sh project
