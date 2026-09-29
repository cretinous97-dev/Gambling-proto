"""Prepare generated artwork for the web.

The image model returns ~1.5-2MB PNGs with a flat backdrop baked in. Serving
those directly would mean a 14MB lobby, so this script does the work once, at
build time, instead of in every visitor's browser:

    * keys out the flat background (sampling the corner pixels)
    * trims the transparent margin and squares the result, so every icon has
      the same optical weight in a grid
    * resizes to a sane maximum
    * writes WebP with alpha (and a PNG fallback for old Safari)

Run after adding or regenerating art:

    ../.venv/bin/python scripts/optimise_art.py

The optimised files are committed, so the Vercel build does not need Pillow.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - tooling only
    sys.exit("Pillow is required:  pip install Pillow")

FRONTEND = Path(__file__).resolve().parent.parent
PUBLIC = FRONTEND / "public"

#: Icons are displayed at ~46px but must survive high-DPI, hence 256.
ICON_MAX = 256
#: The hero is a wide banner; 1600 is plenty for a 2x display.
HERO_MAX = 1600


#: The site palette (frontend/src/styles.css). Thumbnails are composited onto
#: exactly these colours so a tile blends into the lobby instead of sitting on
#: it like a sticker.
TILE_TOP = (27, 36, 64)      # --panel-2
TILE_BOTTOM = (16, 22, 39)   # --bg-2


def composite_on_theme(img: Image.Image) -> Image.Image:
    """Flatten transparency onto a themed vertical gradient.

    Generated icons carry a radial glow that does not survive background
    removal cleanly; laying them back over the site's own navy keeps the glow
    and makes every tile consistent.
    """
    img = img.convert("RGBA")
    width, height = img.size
    backdrop = Image.new("RGBA", (width, height))
    px = backdrop.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        row = tuple(
            int(TILE_TOP[i] + (TILE_BOTTOM[i] - TILE_TOP[i]) * t) for i in range(3)
        )
        for x in range(width):
            px[x, y] = (*row, 255)
    backdrop.alpha_composite(img)
    return backdrop


def round_corners(img: Image.Image, radius_fraction: float = 0.14) -> Image.Image:
    """Round the corners so the art reads as a tile rather than a pasted square.

    The generated art has a radial-gradient backdrop, not a flat one, so keying
    it out leaves a halo. Rounding is both cleaner and honest about what the
    image is: a game thumbnail, exactly like a real casino lobby.
    """
    from PIL import ImageDraw

    img = img.convert("RGBA")
    side = min(img.size)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, img.size[0] - 1, img.size[1] - 1),
        radius=int(side * radius_fraction),
        fill=255,
    )
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def report(path: Path, before: int, after: int) -> None:
    saved = (1 - after / before) * 100
    print(f"  {path.name:16s} {before / 1024:8.0f} KB -> {after / 1024:7.1f} KB  ({saved:.0f}% smaller)")


def process_icon(source: Path) -> None:
    original = source.stat().st_size
    img = Image.open(source)
    # crop to square first, otherwise rounding distorts the aspect ratio
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize(
        (ICON_MAX, ICON_MAX), Image.LANCZOS
    )
    img = composite_on_theme(img)
    img = round_corners(img)

    webp = source.with_suffix(".webp")
    img.save(webp, "WEBP", quality=88, method=6)
    # PNG fallback: cheap at this size, and removes any browser caveat.
    img.save(source, "PNG", optimize=True)
    report(source, original, webp.stat().st_size)


def process_hero(source: Path) -> None:
    original = source.stat().st_size
    img = Image.open(source).convert("RGB")
    if img.width > HERO_MAX:
        ratio = HERO_MAX / img.width
        img = img.resize((HERO_MAX, int(img.height * ratio)), Image.LANCZOS)
    webp = source.with_suffix(".webp")
    img.save(webp, "WEBP", quality=82, method=6)
    img.save(source, "JPEG", quality=82, optimize=True, progressive=True)
    report(source, original, webp.stat().st_size)


def process_logo(source: Path) -> None:
    original = source.stat().st_size
    img = Image.open(source)
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((192, 192), Image.LANCZOS)
    img = composite_on_theme(img)
    img = round_corners(img, radius_fraction=0.22)
    webp = source.with_suffix(".webp")
    img.save(webp, "WEBP", quality=90, method=6)
    img.save(source, "PNG", optimize=True)
    report(source, original, webp.stat().st_size)


def main() -> None:
    print("optimising game tiles (squared, rounded, WebP + PNG fallback)")
    games = sorted((PUBLIC / "games").glob("*.png"))
    for icon in games:
        process_icon(icon)

    hero = PUBLIC / "brand" / "hero.jpg"
    if hero.exists():
        print("\noptimising the hero banner")
        process_hero(hero)

    logo = PUBLIC / "brand" / "logo.png"
    if logo.exists():
        print("\noptimising the brand mark")
        process_logo(logo)

    total = sum(f.stat().st_size for f in PUBLIC.rglob("*") if f.is_file())
    webp_total = sum(
        f.stat().st_size for f in PUBLIC.rglob("*.webp") if f.is_file()
    )
    print(f"\npublic/ payload: {total / 1024 / 1024:.1f} MB "
          f"(WebP assets: {webp_total / 1024 / 1024:.2f} MB)")
    print("done - commit the results")


if __name__ == "__main__":
    main()
