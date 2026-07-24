"""Merge the freeway layout's CSP lights into the Lake Murray track's ext_config.

Both layouts share one track folder but ship as two separate kn5s (models_full.ini / models_freeway.ini).
CSP [LIGHT_N] entries are GLOBAL to the track — they are NOT transformed by a MODEL's POSITION offset —
so the freeway lamp lights, generated in the freeway kn5's own local frame, must be translated by the
same offset the freeway MODEL uses (models_freeway.ini) or they'd float off where the freeway isn't.

This rebuilds build/<full_slug>/extension/ext_config.ini as:
  <full ext_config, verbatim: INCLUDE + LIGHTING + LIGHT_POLLUTION + GRASS_FX + full's LIGHT_0..N>
  + <freeway's LIGHT_* renumbered to continue, each POSITION shifted by the freeway model offset>
  + <full's MATERIAL_ADJUSTMENT_STREETLIGHTS — one section covers LIGHTS_mat in BOTH kn5s>

Run per-project ext_config for `project` and `project_freeway` FIRST — from the central engine:
`(cd .engine && python3 -m scripts.ac.ext_config <ABS-project-dir>)` (both must carry the
[INCLUDE] common/conditions.ini fix), then:  python -m scripts.ac.combine_layouts
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FULL = REPO / "project" / "build" / "san_diego_lake_murray"
FREEWAY_EXT = REPO / "project_freeway" / "build" / "san_diego_freeway_loop" / "extension" / "ext_config.ini"
LIGHT_RE = re.compile(r"^\[LIGHT_(\d+)\]\s*$")


def _blocks(text: str):
    """Split an ext_config into (header, body_lines_including_header) blocks, keyed by section header.
    Leading comment/blank lines attach to the block they precede."""
    lines = text.splitlines()
    out, cur, pre = [], None, []
    for ln in lines:
        if ln.startswith("[") and ln.rstrip().endswith("]"):
            if cur is not None:
                out.append(cur)
            cur = {"header": ln.strip(), "pre": pre, "body": [ln]}
            pre = []
        elif cur is None:
            pre.append(ln)                       # file-leading comments/blank (kept as a preamble block)
        else:
            cur["body"].append(ln)
    if cur is not None:
        out.append(cur)
    return pre, out


def _shift_position(body, off):
    ox, oy, oz = off
    new = []
    for ln in body:
        m = re.match(r"\s*POSITION\s*=\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)", ln)
        if m:
            x, y, z = (float(m.group(i)) for i in (1, 2, 3))
            new.append(f"POSITION = {round(x + ox, 3)}, {round(y + oy, 3)}, {round(z + oz, 3)}")
        else:
            new.append(ln)
    return new


def _read_offset(models_ini: Path):
    for ln in models_ini.read_text().splitlines():
        m = re.match(r"\s*POSITION\s*=\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)", ln)
        if m:
            return tuple(float(m.group(i)) for i in (1, 2, 3))
    raise SystemExit(f"no POSITION in {models_ini}")


def main() -> None:
    base_ext = FULL / "extension" / "ext_config.ini"
    off = _read_offset(FULL / "models_freeway.ini")

    base_pre, base_blocks = _blocks(base_ext.read_text())
    base_lights = [b for b in base_blocks if LIGHT_RE.match(b["header"])]
    n_base = 1 + max(int(LIGHT_RE.match(b["header"]).group(1)) for b in base_lights)   # 243

    _, fw_blocks = _blocks(FREEWAY_EXT.read_text())
    fw_lights = [b for b in fw_blocks if LIGHT_RE.match(b["header"])]

    # renumber freeway lights to continue after the base set, and shift each POSITION by the model offset
    merged_fw = []
    for i, b in enumerate(fw_lights):
        idx = n_base + i
        body = _shift_position(b["body"][1:], off)     # skip the old header line
        merged_fw.append(f"[LIGHT_{idx}]\n" + "\n".join(body))

    # reassemble base verbatim, injecting the freeway lights right before the material-adjustment section
    out_lines, injected = [], False
    if base_pre:
        out_lines += base_pre
    for b in base_blocks:
        if b["header"].startswith("[MATERIAL_ADJUSTMENT") and not injected:
            out_lines.append(f"; --- Freeway-layout streetlights: {len(fw_lights)} lights "
                             f"(freeway-local + model offset {off}) ---")
            out_lines.append("")
            out_lines.append("\n\n".join(merged_fw))
            out_lines.append("")
            injected = True
        if b["pre"]:
            out_lines += b["pre"]
        out_lines += b["body"]
    if not injected:                                    # no material section? append at end
        out_lines.append("\n\n".join(merged_fw))

    base_ext.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
    total = n_base + len(fw_lights)
    print(f"[combine_layouts] merged {len(fw_lights)} freeway lights (offset {off}) "
          f"after {n_base} full lights -> {total} total in {base_ext}")


if __name__ == "__main__":
    main()
