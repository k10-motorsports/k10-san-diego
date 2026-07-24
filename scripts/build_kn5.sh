#!/usr/bin/env bash
# Stand up the AC track from loop.blend: prep -> kn5 -> verify -> ground gate -> track folder ->
# installable zip. Needs Blender 4.2 (the jwl-7 AC Tools addon target).
# Usage: scripts/build_kn5.sh [project-dir]
#
# CENTRAL ENGINE: stages the engine covers run from .engine/ (prodrive-ac-builder at the tag in
# .engine-version; ./bootstrap.sh fetches it). The Blender-first LOOP path is still landing in the
# engine (prodrive-ac-builder PR "Loop front-end: SD's Blender-first build as build_kn5_loop"), so
# at the current pin (v0.15.0) the loop-specific stages below stay LOCAL, each with a marked TODO
# to flip once the engine tag containing the loop front-end is pinned.
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

echo "━━ prep loop.blend -> AC-ready .blend"
# TODO(engine pin > v0.15.0): flip to the engine's loop front-end once the "Loop front-end" PR is
# tagged:  "$BL" --background --python "$ENGINE/scripts/ac/build_kn5_loop.py" -- "$PROJ_ABS"
# and delete scripts/ac/build_kn5.py here. Until then the loop program is LOCAL (the engine's
# build_kn5.py is the OBJ pipeline — a DIFFERENT program; never point this stage at it).
"$BL" --background --python scripts/ac/build_kn5.py       -- "$PROJ" | grep -iE "flipped|prepped|WARNING" || true
echo "━━ export kn5 (vendored AC Tools addon, Blender 4.2)"
# TODO(engine pin > v0.15.0): flip to "$ENGINE/scripts/ac/export_kn5_addon.py". LOCAL for now: the
# engine addon derives materials from the ENGINE pbr table, which only gains the loop prefixes
# (1KERB_SIDEWALK, 1WALL_GUARD, PALMTRUNK/PALMFROND, HOUSE, ...) in the loop-front-end PR — at
# v0.15.0 those would export textureless (= BLACK in-engine). Then delete scripts/ac/pbr.py +
# scripts/ac/export_kn5_addon.py here.
"$BL" --background --python scripts/ac/export_kn5_addon.py -- "$PROJ" | grep -iE "operator|KN5 EXISTS|INI mat" || true
echo "━━ verify (drivability gate, central engine)"
(cd "$ENGINE" && "$PY" -m scripts.ac.verify_kn5 "$PROJ_ABS")
echo "━━ ground coherence (kn5 gate: double-sheet terrain, prop feet on dirt, road support)"
# TODO(engine pin > v0.15.0): flip to (cd "$ENGINE" && "$PY" -m scripts.ac.kn5_ground_check ...)
# once the engine gains the Blender-authored kn5-internal mode + generic 1ROAD bucket (loop
# front-end PR); at v0.15.0 it requires generated OBJs and CO mesh names. Then delete
# scripts/ac/kn5_ground_check.py here. Do NOT fake OBJs to force the engine gate early.
"$PY" -m scripts.ac.kn5_ground_check "$PROJ"
echo "━━ track folder (surfaces/ui/map/models)"
# TODO(engine pin > v0.15.0): flip to (cd "$ENGINE" && "$PY" -m scripts.ac.track_folder ...) once
# the engine emits the WALL surface entry for loop projects (loop front-end PR) — at v0.15.0 it
# would drop WALL and the loop's physical 1WALL_guard rails lose their surface definition. Then
# delete scripts/ac/{track_folder,ext_config,install,credits}.py here (and scripts/ac/verify_kn5.py
# once nothing local still imports its _parse helper).
"$PY" -m scripts.ac.track_folder "$PROJ" | sed 's/^/   /'
cp "$PROJ/build/$SLUG.kn5" "$PROJ/build/$SLUG/"
( cd "$PROJ/build" && rm -f "${SLUG}_track.zip" && zip -qr "${SLUG}_track.zip" "$SLUG" )
echo "✓ installable track -> $PROJ/build/${SLUG}_track.zip"
