"""Regenerate the product icons from the source artwork.

    .venv/bin/python deploy/make_icons.py "<path to source png>"

Source: a square artwork (gear + arrows + chart) on a slate-blue rounded
square, white outside the corners, a small sparkle bottom-right.

Outputs (src/dashboard_web/static/icons/):
  favicon-32.png / favicon-64.png   artwork only, slate keyed to transparent,
                                    sparkle dropped, tight-cropped to the art
  apple-touch-icon-180.png          opaque slate square (iOS masks its own corners)
  favicon.ico                       32 + 16 layers of the transparent favicon
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops

OUT = Path(__file__).resolve().parents[1] / "src" / "dashboard_web" / "static" / "icons"
SLATE = (110, 127, 147)


def _soft_key(img: Image.Image, color: tuple, lo: int, hi: int) -> Image.Image:
    """Alpha mask: 0 where the pixel is within `lo` of `color`, 255 beyond
    `hi`, linear in between — the ramp keeps anti-aliased edges soft."""
    flat = Image.new("RGB", img.size, color)
    diff = ImageChops.difference(img.convert("RGB"), flat).convert("L", (1, 1, 1, 0))
    # 'L' via (1,1,1,0) sums the channels: manhattan distance, clipped at 255
    return diff.point(lambda d: 0 if d <= lo else 255 if d >= hi else (d - lo) * 255 // (hi - lo))


def build(src: Path) -> None:
    im = Image.open(src).convert("RGB")
    # crop to the rounded square: everything that is not the white margin
    square = im.crop(_soft_key(im, (255, 255, 255), 10, 30).getbbox())
    w, h = square.size

    # ── apple-touch-icon: opaque slate square, white corners painted slate ──
    white = _soft_key(square, (255, 255, 255), 10, 40)
    touch = Image.composite(square, Image.new("RGB", square.size, SLATE), white)
    OUT.mkdir(parents=True, exist_ok=True)
    touch.resize((180, 180), Image.LANCZOS).save(OUT / "apple-touch-icon-180.png")

    # ── favicon: key out slate, keep only the central artwork (drops sparkle) ──
    alpha = _soft_key(square, SLATE, 45, 110)
    keep = Image.new("L", square.size, 0)
    from PIL import ImageDraw
    r = int(min(w, h) * 0.40)
    ImageDraw.Draw(keep).ellipse((w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r), fill=255)
    alpha = ImageChops.multiply(alpha, keep)
    art = square.copy()
    art.putalpha(alpha)
    art = art.crop(alpha.getbbox())
    # square canvas so the icon does not stretch
    side = max(art.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(art, ((side - art.width) // 2, (side - art.height) // 2))
    for n in (64, 32):
        canvas.resize((n, n), Image.LANCZOS).save(OUT / f"favicon-{n}.png")
    canvas.resize((32, 32), Image.LANCZOS).save(OUT / "favicon.ico", sizes=[(32, 32), (16, 16)])


if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print("wrote", sorted(p.name for p in OUT.iterdir()))
