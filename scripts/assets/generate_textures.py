"""Regenerate every bundled track texture from a redistributable source — so ``assets/textures/`` never
carries a byte we can't ship. Two provenance classes, both CC0-1.0 / public domain (see assets/licenses.json):

  * Photoreal surface / building maps  -> fetched from ambientCG (CC0 material scans), resized to the
    exact power-of-two the pipeline expects (non-PoT textures hard-crash CSP on load).
  * Palms + kerb                       -> ORIGINAL procedural art authored here (numpy + PIL), deterministic
    (fixed seed) so a clean checkout reproduces them bit-for-bit.

Palm frond / atlas are alpha-cutout billboards: their alpha is written BINARY (0/255) so EEVEE-Next's
alpha hashing and the in-engine kn5 ALPHATEST cut the card out crisply (a soft 0..1 alpha dithers the thin
leaflets away). Filenames + sizes match what the engine pbr table (.engine/scripts/ac/pbr.py) binds by
prefix, so nothing downstream changes.

    python -m scripts.assets.generate_textures                 # fetch CC0 + regen procedural
    python -m scripts.assets.generate_textures --skip-download # regen the 4 procedural textures only (offline)
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

TEX = Path(__file__).resolve().parents[2] / "assets" / "textures"

# ambientCG CC0 material -> which map goes to which shipped file (dst, size, keep_alpha).
# suffix "Color" = albedo, "NormalGL" = OpenGL-convention normal (matches Blender/AC).
AMBIENTCG = [
    ("Asphalt014", "2K", [("Color", "asphalt_cracked_diffuse.jpg", 2048, False),
                          ("NormalGL", "asphalt_cracked_normal.jpg", 2048, False)]),
    ("Grass004", "2K", [("Color", "grass_diffuse.jpg", 2048, False),
                        ("NormalGL", "grass_normal.jpg", 2048, False)]),
    ("Ground048", "1K", [("Color", "ground_dry_diffuse.jpg", 1024, False)]),
    ("Concrete034", "1K", [("Color", "building_diffuse.jpg", 1024, False),
                           ("NormalGL", "building_normal.jpg", 1024, False)]),
    ("PaintedPlaster006", "1K", [("Color", "stucco_diffuse.jpg", 1024, False)]),
    ("Plaster004", "2K", [("Color", "house_diffuse.png", 2048, True)]),
]


def fetch_ambientcg() -> None:
    for asset, res, maps in AMBIENTCG:
        zurl = f"https://ambientcg.com/get?file={asset}_{res}-JPG.zip"
        print(f"[tex] fetch {asset}_{res}-JPG …")
        with urllib.request.urlopen(zurl, timeout=180) as r:
            zf = zipfile.ZipFile(io.BytesIO(r.read()))
        for suffix, dst, size, alpha in maps:
            member = f"{asset}_{res}-JPG_{suffix}.jpg"
            im = Image.open(io.BytesIO(zf.read(member))).convert("RGB")
            if im.size != (size, size):
                im = im.resize((size, size), Image.LANCZOS)
            if dst.endswith(".png"):
                im.convert("RGBA" if alpha else "RGB").save(TEX / dst, "PNG", optimize=True)
            else:
                q = 85 if suffix == "NormalGL" else 92        # normals: no chroma detail to preserve
                im.save(TEX / dst, "JPEG", quality=q, subsampling=0)
            print(f"       -> {dst} {im.size}")


# --------------------------------------------------------------------------- procedural helpers
def _tiled_grain(rng, h, w, cell):
    """Seamless value-noise: tile a small random grid (periodic by construction) then wrap-blur."""
    small = rng.random((h // cell, w // cell)).astype(np.float32)
    big = np.kron(small, np.ones((cell, cell), np.float32))[:h, :w]
    acc = sum(np.roll(np.roll(big, dy, 0), dx, 1)
              for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    return acc / 9.0


def _binarize_alpha(im, thresh=90):
    arr = np.asarray(im).copy()
    arr[..., 3] = np.where(arr[..., 3] > thresh, 255, 0).astype(np.uint8)
    return Image.fromarray(arr)


# --------------------------------------------------------------------------- procedural textures
def gen_kerb() -> None:
    S = 256
    xx = np.arange(S)
    band = (xx // 64) % 2                                # R W R W, tiles seamlessly
    red, white = np.array([196, 32, 30.]), np.array([232, 230, 226.])
    img = np.where(band[None, :, None] == 0, red, white) * np.ones((S, 1, 1), np.float32)
    grain = (_tiled_grain(np.random.default_rng(2024), S, S, 8) - 0.5) * 26.0
    img = np.clip(img + grain[..., None], 0, 255)
    seam = ((xx % 64) < 2) | ((xx % 64) > 61)            # dark grout line between blocks
    img[:, seam] *= 0.72
    Image.fromarray(img.astype(np.uint8)).save(TEX / "kerb_diffuse.png")
    print("[tex] -> kerb_diffuse.png 256x256")


def gen_bark() -> None:
    S = 256
    rng = np.random.default_rng(2024)
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    base = np.array([118, 104, 82.])
    stri = 0.10 * np.sin(xx / S * 2 * np.pi * 26) + 0.06 * np.sin(xx / S * 2 * np.pi * 61 + 1.3)
    scar = 0.14 * (np.sin(yy / S * 2 * np.pi * 7.5) ** 8)          # Mexican-fan-palm frond scars
    grain = (_tiled_grain(rng, S, S, 4) - 0.5) * 0.22
    shade = 1.0 + stri - scar + grain
    rgb = np.clip(base[None, None, :] * shade[..., None], 0, 255).astype(np.uint8)
    rgba = np.dstack([rgb, np.full((S, S), 255, np.uint8)])        # opaque (PALMTRUNK ignores alpha)
    Image.fromarray(rgba).save(TEX / "palm_bark.png")
    print("[tex] -> palm_bark.png 256x256")


def gen_frond() -> None:
    W, H = 512, 1024
    rng = np.random.default_rng(7)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, n = W / 2, 140
    spine = [(cx + 30 * np.sin((i / n) * 2.2), H * (1 - i / n), i / n) for i in range(n + 1)]
    for i in range(2, n - 1, 2):                          # every other node -> transparent V-gaps
        x, y, t = spine[i]
        env = np.sin(np.pi * t) ** 0.6                     # longest leaflets mid-frond
        L = 175 * env + 6
        for s in (-1, 1):
            ang = s * (0.95 - 0.35 * t)
            ex, ey = x + L * np.sin(ang), y - L * np.cos(ang) * 0.62 - L * 0.35
            g = max(40, min(190, int(78 + 55 * env) + int(rng.integers(-12, 12))))
            d.line([(x, y), (ex, ey)], fill=(int(26 + 30 * (1 - env)), g, int(22 + 16 * env), 255), width=2)
    for i in range(n):                                     # rachis
        a, b = spine[i], spine[i + 1]
        d.line([(a[0], a[1]), (b[0], b[1])], fill=(70, 54, 30, 255), width=4)
    _binarize_alpha(im).save(TEX / "palm_frond.png")
    print("[tex] -> palm_frond.png 512x1024 (binary alpha)")


def gen_atlas() -> None:
    S = 1024
    rng = np.random.default_rng(11)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    half = S // 2
    for cell in range(4):
        ox, oy = (cell % 2) * half, (cell // 2) * half
        bx, by, top = ox + half / 2, oy + half - 20, oy + 70
        for k in range(60):                                # trunk
            t = k / 59
            y = by + (top - by) * t
            w = 15 * (1 - 0.45 * t)
            d.rectangle([bx - w / 2, y, bx + w / 2, y + 8], fill=(120, 104, 82, 255))
        nf = 11 + cell                                     # crown of radiating fronds
        for f in range(nf):
            ang = np.pi * (0.12 + 0.76 * f / (nf - 1))
            L = 150 + 30 * np.sin(f * 1.7)
            ex, ey = bx + L * np.cos(ang) * (1.05 if f % 2 == 0 else 1.0), top - L * np.sin(ang) * 0.9
            g = int(90 + 40 * np.sin(f))
            d.line([(bx, top), (ex, ey)], fill=(34, g, 38, 255), width=7)
            for u in (0.4, 0.7):
                mx, my = bx + (ex - bx) * u, top + (ey - top) * u
                d.line([(mx, my), (mx + 16, my - 22)], fill=(30, g, 34, 255), width=3)
    _binarize_alpha(im).save(TEX / "palms_atlas.png")
    print("[tex] -> palms_atlas.png 1024x1024 (binary alpha)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-download", action="store_true",
                    help="regenerate only the offline procedural textures (no network)")
    args = ap.parse_args()
    TEX.mkdir(parents=True, exist_ok=True)
    if not args.skip_download:
        fetch_ambientcg()
    gen_kerb(); gen_bark(); gen_frond(); gen_atlas()
    print("[tex] done — all bundled textures are CC0 (see assets/licenses.json)")


if __name__ == "__main__":
    sys.exit(main())
