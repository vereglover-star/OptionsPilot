"""Generate assets/optionspilot.ico — the app icon.

Design: dark rounded tile in the dashboard palette, three candlesticks
(two accent-blue, one green marking the winner) with an upward drift.
Drawn at 256px and downsampled into a multi-resolution .ico.
Rerun after any palette change:  .venv\\Scripts\\python scripts\\make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

BG = (26, 26, 25, 255)        # --surface
ACCENT = (57, 135, 229, 255)  # --accent
GREEN = (12, 163, 12, 255)    # --good
OUT = Path(__file__).resolve().parents[1] / "assets" / "optionspilot.ico"


def draw_tile(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = size * 0.22
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    # three candles: (x-center frac, body top frac, body bottom frac,
    #                 wick top frac, wick bottom frac, color)
    candles = [
        (0.28, 0.48, 0.72, 0.40, 0.80, ACCENT),
        (0.50, 0.34, 0.62, 0.26, 0.70, ACCENT),
        (0.72, 0.20, 0.50, 0.13, 0.58, GREEN),
    ]
    body_w = size * 0.13
    wick_w = max(size * 0.030, 1)
    for cx, bt, bb, wt, wb, color in candles:
        x = cx * size
        d.rectangle([x - wick_w / 2, wt * size, x + wick_w / 2, wb * size],
                    fill=color)
        d.rounded_rectangle(
            [x - body_w / 2, bt * size, x + body_w / 2, bb * size],
            radius=size * 0.03, fill=color,
        )
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tile = draw_tile(256)
    tile.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                          (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
