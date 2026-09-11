"""Renders the app icon (used for the .exe and the Startup shortcut).

The same language as the widget: a Claude-orange tile with a usage pill across
it, and the skull that the widget shows at 100%.
"""

import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets", "ClaudeUsageBar.ico")
ORANGE = (201, 100, 66, 255)
INK = (240, 238, 235, 255)


def skull(img, box, fill):
    """The widget's skull, drawn straight rather than imported, so building the
    icon never depends on importing the app itself."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hole = (0, 0, 0, 0)

    def P(u, v):
        return (x0 + u * w, y0 + v * h)

    def ell(u0, v0, u1, v1, color):
        d.ellipse([P(u0, v0), P(u1, v1)], fill=color)

    def rect(u0, v0, u1, v1, color, r=0.0):
        pts = [P(u0, v0), P(u1, v1)]
        if r:
            d.rounded_rectangle(pts, radius=max(1, r * w), fill=color)
        else:
            d.rectangle(pts, fill=color)

    ell(0.00, 0.00, 1.00, 0.82, fill)
    rect(0.24, 0.55, 0.76, 1.00, fill, r=0.14)
    ell(0.08, 0.24, 0.45, 0.60, hole)
    ell(0.55, 0.24, 0.92, 0.60, hole)
    d.polygon([P(0.50, 0.58), P(0.38, 0.75), P(0.62, 0.75)], fill=hole)
    rect(0.27, 0.80, 0.73, 0.855, hole)
    for u in (0.42, 0.58):
        rect(u - 0.03, 0.855, u + 0.03, 1.00, hole)
    img.alpha_composite(layer)


def tile(size):
    ss = 8
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=ORANGE)

    pad, bar_h = s * 0.16, s * 0.17
    top = s * 0.66
    d.rounded_rectangle((pad, top, s - pad, top + bar_h), radius=bar_h / 2, fill=(0, 0, 0, 90))
    d.rounded_rectangle((pad, top, pad + (s - 2 * pad) * 0.72, top + bar_h),
                        radius=bar_h / 2, fill=INK)
    skull(img, (s * 0.30, s * 0.12, s * 0.70, s * 0.56), INK)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sizes = (256, 128, 64, 48, 32, 16)
    frames = [tile(n) for n in sizes]
    frames[0].save(OUT, format="ICO", sizes=[(n, n) for n in sizes])
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
