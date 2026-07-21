"""Mesh-file helpers shared by the NETWORK pipeline (build_network_mesh / build_network_env).

Ported from prodrive-ac-builder's build_mesh.py, which is a full loop-track mesh builder there. This
repo builds its loop track live in Blender (loop.blend is the truth), so only the four pure helpers
the network pipeline imports are carried over — the loop-building body stays behind.
"""

from __future__ import annotations

import ast
import math
import struct
from pathlib import Path

from scripts.geometry.projection import _meters_per_degree

Vertex = tuple[float, float, float]


def read_npy(path: Path) -> list[list[float]]:
    """Minimal float64 2D .npy reader (matches the writer in elevation/heightfield.py)."""
    with open(path, "rb") as f:
        assert f.read(6) == b"\x93NUMPY"
        f.read(2)
        hlen = struct.unpack("<H", f.read(2))[0]
        header = ast.literal_eval(f.read(hlen).decode())
        ny, nx = header["shape"]
        data = struct.unpack(f"<{ny * nx}d", f.read(8 * ny * nx))
    return [list(data[j * nx:(j + 1) * nx]) for j in range(ny)]


def project_grid(grid: list[list[float]], meta: dict, origin: tuple[float, float], elev0: float,
                 *, mirror_x: bool = False) -> list[list[Vertex]]:
    """Project the lat/lon terrain grid into the same local ENU frame as the centerline (mirroring the
    east axis too when ``mirror_x`` so the grass stays under the mirrored road)."""
    s, w, n, e = meta["bbox_swne"]
    nx, ny, sp = meta["nx"], meta["ny"], meta["spacing_m"]
    midlat = (s + n) / 2
    gy = sp / 111_000.0
    gx = sp / (111_000.0 * math.cos(math.radians(midlat)))
    lon0, lat0 = origin
    m_lon, m_lat = _meters_per_degree(lat0)
    sx = -1.0 if mirror_x else 1.0
    out = []
    for j in range(ny):
        lat = n - j * gy
        row = [(sx * ((w + i * gx) - lon0) * m_lon, grid[j][i] - elev0, (lat - lat0) * m_lat) for i in range(nx)]
        out.append(row)
    return out


def write_obj(path: Path, mtl_name: str, groups: list[tuple[str, str, dict]]) -> tuple[int, int]:
    """Write an OBJ with named objects/materials. groups: (object_name, material, mesh). Emits per-
    vertex UVs (``vt`` + ``f v/vt``) for any mesh carrying a parallel ``uvs`` list; v and vt use
    independent global offsets so textured and untextured groups can share one file."""
    lines = [f"mtllib {mtl_name}"]
    voff = vtoff = 0
    nv = nf = 0
    for name, mat, mesh in groups:
        lines.append(f"o {name}")
        lines.append(f"usemtl {mat}")
        for x, y, z in mesh["vertices"]:
            lines.append(f"v {x:.3f} {y:.3f} {z:.3f}")
        uvs = mesh.get("uvs")
        if uvs:
            for u, v in uvs:
                lines.append(f"vt {u:.4f} {v:.4f}")
        for a, b, c in mesh["tris"]:
            if uvs:
                lines.append(f"f {a+1+voff}/{a+1+vtoff} {b+1+voff}/{b+1+vtoff} {c+1+voff}/{c+1+vtoff}")
            else:
                lines.append(f"f {a + 1 + voff} {b + 1 + voff} {c + 1 + voff}")
        voff += len(mesh["vertices"])
        if uvs:
            vtoff += len(uvs)
        nv += len(mesh["vertices"])
        nf += len(mesh["tris"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return nv, nf


def orient_up(mesh: dict) -> dict:
    """Flip any triangle whose geometric normal faces down so every face points +Y (up).

    AC track collision is **one-sided**: a ground/drivable surface whose faces point down lets the
    car drop straight through from above (and renders backface-culled). The ribbon/terrain/kerb
    generators wind their quads downward, so re-orient them up before export. Mutates and returns
    the mesh. Only safe for near-horizontal surfaces — don't apply to vertical geometry (barriers)."""
    V = mesh["vertices"]
    out = []
    for a, b, c in mesh["tris"]:
        ax, _, az = V[a]
        bx, _, bz = V[b]
        cx, _, cz = V[c]
        ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)  # y-component of (b-a)×(c-a)
        out.append((a, c, b) if ny < 0.0 else (a, b, c))
    mesh["tris"] = out
    return mesh
