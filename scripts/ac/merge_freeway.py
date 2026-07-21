"""Translate the freeway NETWORK's built OBJs into the Lake Murray LOOP's local frame, so build_kn5 can
import both into ONE "K10 - San Diego" kn5 (the loop + the freeway box, 12 km apart, at their real
relative position — "connect them eventually").

Both projects build in the same ENU convention (x=east, y=up, z=north) but about DIFFERENT origins, and
with elev0 = each project's own min height. This shifts every freeway vertex by the origin delta
(dx,dz) + the elevation-datum delta (dy = fw_elev0 - loop_elev0), so the freeway lands at its true
geographic offset and true elevation relative to the loop. Group names get a ``_fw`` suffix (keeping the
1ROAD_/1GRASS_/1WALL_/HWYSTRUCT/MARKINGS prefix so materials + physics still bind).

Requires BOTH projects to share mirror_x (both false) — enforced here. Run after the freeway mesh + env
are built (unmirrored) and before the loop's build_kn5:

    python -m scripts.ac.merge_freeway project project_freeway_net
    -> project/data/track_freeway.obj, project/data/env_freeway.obj
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.geometry.projection import _meters_per_degree

# Drop freeway env groups that have no redistributable texture (would render BLACK in-engine): the mined
# bushes atlas + the CSP-water plane were removed in the CC0 pass. The freeway's value is its roads +
# structures + terrain (track.obj); its light poles survive. MOUNTAINS dropped (the loop has its own context).
DROP_ENV = ("MOUNTAINS", "BUSHES", "WATER")


def _transform_obj(inp: Path, outp: Path, dx: float, dy: float, dz: float, suffix: str, drop=()) -> tuple[int, int]:
    """Translate every vertex by (dx,dy,dz), suffix each group name (prefix kept), drop groups by prefix,
    re-index v/vt so the output is a standalone OBJ."""
    verts: list = []; vts: list = []; groups: list = []; cur = None
    for ln in inp.read_text().splitlines():
        s = ln.split()
        if not s:
            continue
        if s[0] == "v":
            verts.append((float(s[1]) + dx, float(s[2]) + dy, float(s[3]) + dz))
        elif s[0] == "vt":
            vts.append((float(s[1]), float(s[2])))
        elif s[0] == "o":
            cur = (ln[2:].strip(), []); groups.append(cur)
        elif s[0] == "f" and cur is not None:
            face = []
            for tok in s[1:]:
                p = tok.split("/")
                vi = int(p[0]) - 1
                ti = int(p[1]) - 1 if len(p) > 1 and p[1] else None
                face.append((vi, ti))
            cur[1].append(face)
    kept = [(n, fs) for n, fs in groups if not any(n.upper().startswith(d) for d in drop)]
    uv: dict = {}; ut: dict = {}; ov: list = []; ot: list = []

    def mv(i):
        if i not in uv:
            uv[i] = len(ov); ov.append(verts[i])
        return uv[i]

    def mt(i):
        if i is None:
            return None
        if i not in ut:
            ut[i] = len(ot); ot.append(vts[i])
        return ut[i]

    body = []
    for n, fs in kept:
        body.append(f"o {n}_{suffix}")
        for face in fs:
            toks = []
            for vi, ti in face:
                a = mv(vi); b = mt(ti)
                toks.append(f"{a + 1}/{b + 1}" if b is not None else f"{a + 1}")
            body.append("f " + " ".join(toks))
    with outp.open("w") as f:
        for x, y, z in ov:
            f.write(f"v {x:.4f} {y:.4f} {z:.4f}\n")
        for u, v in ot:
            f.write(f"vt {u:.5f} {v:.5f}\n")
        f.write("\n".join(body) + "\n")
    return len(ov), len(kept)


def build(loop_dir: str | Path, fw_dir: str | Path) -> dict:
    loop = Path(loop_dir); fw = Path(fw_dir)
    L = json.loads((loop / "data" / "centerline.local.json").read_text())["origin"]
    Fmeta = json.loads((fw / "data" / "network.local.json").read_text())
    F = Fmeta["origin"]
    if Fmeta.get("mirror_x", False):
        raise SystemExit("freeway network is mirror_x=True — rebuild it with mirror_x=false to match the "
                         "loop before merging (set track.config.json mirror_x=false + rebuild mesh/env).")
    m_lon, m_lat = _meters_per_degree(L["lat"])
    dx = (F["lon"] - L["lon"]) * m_lon
    dz = (F["lat"] - L["lat"]) * m_lat
    dy = F["elev_m"] - L["elev_m"]

    out = {}
    tv, tg = _transform_obj(fw / "data" / "track.obj", loop / "data" / "track_freeway.obj", dx, dy, dz, "fw")
    out["track"] = (tv, tg)
    envp = fw / "data" / "environment.obj"
    if envp.exists():
        ev, eg = _transform_obj(envp, loop / "data" / "env_freeway.obj", dx, dy, dz, "fw", drop=DROP_ENV)
        out["env"] = (ev, eg)
    print(f"[merge_freeway] offset dx={dx:.0f} dz={dz:.0f} dy={dy:.1f} m "
          f"({(dx * dx + dz * dz) ** 0.5 / 1000:.1f} km from loop)")
    for k, (v, g) in out.items():
        print(f"[merge_freeway] {k}: {v} verts, {g} groups -> project/data/{k}_freeway.obj")
    return out


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: python -m scripts.ac.merge_freeway <loop-dir> <freeway-net-dir>")
    build(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
