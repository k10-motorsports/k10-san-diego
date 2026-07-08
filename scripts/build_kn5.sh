#!/usr/bin/env bash
# Stand up the AC track from loop.blend: prep -> kn5 -> verify -> track folder -> installable zip.
# Needs Blender 4.2 (the jwl-7 AC Tools addon target). Usage: scripts/build_kn5.sh [project-dir]
set -euo pipefail
PROJ="${1:-project}"
BL="${BLENDER:-$HOME/.cache/prodrive-ac-builder/Blender4.2.9.app/Contents/MacOS/Blender}"
SLUG="$(python3 -c "import json,sys;print(json.load(open('$PROJ/track.config.json'))['slug'])")"

echo "━━ prep loop.blend -> AC-ready .blend"
"$BL" --background --python scripts/ac/build_kn5.py       -- "$PROJ" | grep -iE "flipped|prepped|WARNING" || true
echo "━━ export kn5 (vendored AC Tools addon, Blender 4.2)"
"$BL" --background --python scripts/ac/export_kn5_addon.py -- "$PROJ" | grep -iE "operator|KN5 EXISTS|INI mat" || true
echo "━━ verify (drivability gate)"
python3 -m scripts.ac.verify_kn5 "$PROJ"
echo "━━ track folder (surfaces/ui/map/models)"
python3 -m scripts.ac.track_folder "$PROJ" | sed 's/^/   /'
cp "$PROJ/build/$SLUG.kn5" "$PROJ/build/$SLUG/"
( cd "$PROJ/build" && rm -f "${SLUG}_track.zip" && zip -qr "${SLUG}_track.zip" "$SLUG" )
echo "✓ installable track -> $PROJ/build/${SLUG}_track.zip"
