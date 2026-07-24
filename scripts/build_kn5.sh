#!/usr/bin/env bash
# Stand up the AC track from loop.blend: prep -> kn5 -> verify -> ground gate -> track folder ->
# installable zip. Needs Blender 4.2 (the jwl-7 AC Tools addon target).
# Usage: scripts/build_kn5.sh [project-dir]
#
# CENTRAL ENGINE: every stage runs from .engine/ (prodrive-ac-builder at the tag in
# .engine-version; ./bootstrap.sh fetches it). Since v0.16.0 the engine carries the Blender-first
# LOOP front-end (build_kn5_loop.py + loop-aware pbr/ground-check/track_folder), so nothing here
# is local any more — SD keeps no copies of engine code.
set -euo pipefail
PROJ="${1:-project}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
BL="${BLENDER:-$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender}"
[[ -d "$ROOT/.engine/scripts" ]] || "$ROOT/bootstrap.sh"
ENGINE="$ROOT/.engine"
PROJ_ABS="$(cd "$PROJ" && pwd)"
SLUG="$($PY -c "import json,sys;print(json.load(open('$PROJ/track.config.json'))['slug'])")"

echo "━━ prep loop.blend -> AC-ready .blend (engine loop front-end)"
# build_kn5_loop.py is the Blender-first program (preps an EXISTING .blend); the engine's
# build_kn5.py is the OBJ pipeline — a DIFFERENT program; never point this stage at it.
# Its over-cap guard is FATAL: every mesh must land under 65,535 verts after the split pass
# (foliage ships pre-chunked from build_loop_blend so uneven spatial bands can't breach it).
"$BL" --background --python "$ENGINE/scripts/ac/build_kn5_loop.py"   -- "$PROJ_ABS"
echo "━━ export kn5 (engine addon, Blender 4.2)"
"$BL" --background --python "$ENGINE/scripts/ac/export_kn5_addon.py" -- "$PROJ_ABS"
echo "━━ verify (drivability gate, central engine)"
(cd "$ENGINE" && "$PY" -m scripts.ac.verify_kn5 "$PROJ_ABS")
echo "━━ ground coherence (engine kn5 gate — kn5-internal mode on Blender-first loops: no source OBJs, physical checks still gate)"
(cd "$ENGINE" && "$PY" -m scripts.ac.kn5_ground_check "$PROJ_ABS")
echo "━━ track folder (surfaces/ui/map/models, central engine)"
(cd "$ENGINE" && "$PY" -m scripts.ac.track_folder "$PROJ_ABS") | sed 's/^/   /'
cp "$PROJ/build/$SLUG.kn5" "$PROJ/build/$SLUG/"
( cd "$PROJ/build" && rm -f "${SLUG}_track.zip" && zip -qr "${SLUG}_track.zip" "$SLUG" )
echo "✓ installable track -> $PROJ/build/${SLUG}_track.zip"
