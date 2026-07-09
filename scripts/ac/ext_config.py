"""Generate the CSP (Custom Shaders Patch) extension/ext_config.ini for the Lake Murray track.

CSP-native + Pure-friendly. It does NOT override the sky or bake lighting, so Pure's sky/sun drives the
scene; this file only adds the track's own layer:
  - one REAL light per streetlight (explicit [LIGHT_N] at each lamp head) that emits at night,
    plus an emissive lamp material so the fixtures glow;
  - GrassFX on 1GRASS (road/kerb/walls occlude it);
  - a warm San-Diego light-pollution sky glow;
  - a gentle global [LIGHTING] lift.

CRITICAL — the night gate: the lights and the emissive are gated on CONDITION = NIGHT_SMOOTH, which is
NOT a built-in CSP condition. It is defined only in CSP's common/conditions.ini. If that file is not
pulled in, NIGHT_SMOOTH is an UNDEFINED condition, CSP evaluates it to 0 (OFF), and NOTHING ever lights
up — this was the "streetlights never emit" bug through v0.5.1. We fix it exactly the way the shipped,
known-working sx_lemans.ini does: an [INCLUDE] of common/conditions.ini at the top of the file.

Why explicit [LIGHT_N] and not [LIGHT_SERIES] MESHES: the posts export as ONE merged LIGHTS mesh, so a
MESHES series drops a single light at the blob centroid (the old "streetlights never emit" bug). One
[LIGHT_N] per lamp-head position always emits, exactly where the pole is. Positions come from
data/lights.local.json (LOCAL frame x=E,y=up,z=N); the kn5's AC world frame is (x, y, -z) — verified
against the exported kn5's road/dummy coordinates.

Run:  python -m scripts.ac.ext_config <project-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _kn5_mesh_names(project_dir: Path, slug: str) -> set[str]:
    kn5 = project_dir / "build" / f"{slug}.kn5"
    if not kn5.exists():
        return set()
    try:
        from scripts.ac.verify_kn5 import _parse
        _dummies, meshes = _parse(kn5)
        return {str(m.get("name", "")) for m in meshes}
    except Exception:
        return set()


def generate(project_dir: str | Path) -> Path:
    project_dir = Path(project_dir)
    data = project_dir / "data"
    cfg = json.loads((project_dir / "track.config.json").read_text())
    slug = cfg["slug"]
    names = _kn5_mesh_names(project_dir, slug)

    def present(prefix):
        return any(n.upper().startswith(prefix) for n in names) or not names

    lcfg = (cfg.get("lighting", {}) or {})
    lp = lcfg.get("light_pollution", {}) or {}
    lp_color = lp.get("color", [1.0, 0.86, 0.66])          # warm coastal-metro white (San Diego)
    lp_density = lp.get("density", 0.06)                   # subtle sky glow (was blowing out the night at 0.2)
    lp_radius_km = lp.get("radius_km", 6.0)
    # Streetlight tuning. With ~250 poles the pools overlap, so per-light brightness must be LOW or the
    # whole scene blows out (the "undriveable at night" bug: was 28). These are gentle, config-overridable.
    st_bright = float(lcfg.get("street_brightness", 3.2))  # [LIGHT] COLOR 4th value (intensity)
    st_range = float(lcfg.get("street_range_m", 20.0))
    st_spot = float(lcfg.get("street_spot_deg", 104.0))
    st_fade = float(lcfg.get("street_fade_at_m", 165.0))
    st_emissive = float(lcfg.get("street_emissive", 0.55)) # lamp-head glow (was 1.6 → bloomed)

    out = [
        "; ============================================================================",
        f"; {cfg.get('name', slug)} — CSP ext_config (Pure-friendly: no sky/lighting override)",
        "; ============================================================================", "",
        "; Pull in CSP's condition table so NIGHT_SMOOTH (used by the streetlights + the lamp",
        "; emissive below) is DEFINED. It is not built in; without this include the condition is",
        "; undefined -> evaluates to OFF -> lights never emit. Shipped sx_lemans.ini does the same.",
        "[INCLUDE]",
        "INCLUDE = common/conditions.ini", "",
        "[LIGHTING]",
        "LIT_MULT = 1.0",
        "BOUNCED_LIGHT_MULT = 1, 1, 1, 0.05", "",
        "; --- warm San Diego light-pollution glow (night only; COLOR is r,g,b 0..1) ---",
        "[LIGHT_POLLUTION]",
        "ACTIVE = 1",
        f"COLOR = {', '.join(str(c) for c in lp_color)}",
        f"DENSITY = {lp_density}",
        f"RADIUS_KM = {lp_radius_km}",
        "RELATIVE_POSITION = -1.5, 0, -2.0", "",
    ]

    # GrassFX on the neighbourhood turf; road/kerb/walls are the ONLY occluders (near-field, safe — a
    # far-field occluder can blow the GrassFX volume past the Windows GPU watchdog; keep the list tight).
    if present("1GRASS"):
        out += ["; --- GrassFX on the turf ----------------------------------------------------",
                "[GRASS_FX]",
                "GRASS_MATERIALS = 1GRASS_mat",
                "OCCLUDING_MESHES = 1ROAD_road, 1KERB_kerb, 1WALL_guard",
                "LENGTH_GAIN = 0.8", "", ]

    # Streetlights: one explicit light per lamp head + an emissive lamp material at night.
    heads = []
    lp_path = data / "lights.local.json"
    if lp_path.exists():
        heads = json.loads(lp_path.read_text()).get("lampheads", [])
    if heads:
        out += [f"; --- Streetlights: {len(heads)} lights, one per pole (AC world = local x,y,-z) ---"]
        for n, (x, y, z) in enumerate(heads):
            out += [f"[LIGHT_{n}]",
                    "ACTIVE = 1",
                    f"POSITION = {x}, {y}, {-z}",           # local (E,up,N) -> AC (x, y up, -z)
                    "DIRECTION = 0, -1, 0",                 # shine down
                    f"COLOR = 255, 214, 150, {st_bright}",  # warm sodium-white — RGB 0-255, last = brightness (LOW: many poles overlap)
                    "COLOR_OFF = 0, 0, 0, 0",               # fully dark when the night condition is false
                    f"SPOT = {st_spot}", "SPOT_SHARPNESS = 0.3",
                    f"RANGE = {st_range}", "RANGE_GRADIENT_OFFSET = 0.2",
                    f"FADE_AT = {st_fade}", "FADE_SMOOTH = 35",
                    "CONDITION = NIGHT_SMOOTH", "SPECULAR_MULT = 0.6", ""]
        out += ["; --- lamp fixtures glow at night --------------------------------------------",
                "[MATERIAL_ADJUSTMENT_STREETLIGHTS]",
                "MATERIALS = LIGHTS_mat",
                "KEY_0 = ksEmissive",
                f"VALUE_0 = 255, 214, 150, {st_emissive}",  # ksEmissive is 0-255 RGB + brightness (gentle glow)
                "VALUE_0_OFF = 0, 0, 0, 0",
                "CONDITION = NIGHT_SMOOTH", ""]

    ext = project_dir / "build" / slug / "extension"
    ext.mkdir(parents=True, exist_ok=True)
    cfg_path = ext / "ext_config.ini"
    cfg_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[ext_config] wrote {cfg_path.name}: {len(heads)} streetlights, GrassFX={present('1GRASS')}")
    return cfg_path


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m scripts.ac.ext_config <project-dir>")
    generate(sys.argv[1])


if __name__ == "__main__":
    main()
