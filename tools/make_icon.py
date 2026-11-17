"""Render packaging/HorizonPitwall.ico from the same mark as frontend/public/favicon.svg.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BG, BAR, LIGHT, ACCENT = (17, 19, 21, 255), (166, 173, 180, 255), (242, 241, 237, 255), (232, 179, 91, 255)
# favicon.svg geometry on a 32-unit grid: (x, y, w, h, colour)
BARS = [(5, 19, 4, 7, BAR), (11, 15, 4, 11, BAR), (17, 10, 4, 16, LIGHT), (23, 6, 4, 20, ACCENT)]


def render(size: int) -> Image.Image:
    s = 8  # supersample, then downscale for clean edges
    k = size * s / 32
    img = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size * s - 1, size * s - 1], radius=round(6 * k), fill=BG)
    for x, y, w, h, c in BARS:
        d.rectangle([round(x * k), round(y * k), round((x + w) * k) - 1, round((y + h) * k) - 1], fill=c)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    out = ROOT / "packaging" / "HorizonPitwall.ico"
    out.parent.mkdir(exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big = render(256)
    big.save(out, format="ICO", sizes=[(n, n) for n in sizes], append_images=[render(n) for n in sizes[:-1]])
    print(out)


if __name__ == "__main__":
    main()
