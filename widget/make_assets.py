"""Generates the package logos, the widget icon and the picker screenshot.

Kept in Python so the artwork matches the tray app: same Claude orange, same
pill-and-bar language, same hand-drawn skull for a spent limit.
"""

import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "claude_usage_bar.pyw")
IMAGES = os.path.join(HERE, "package", "Images")
ASSETS = os.path.join(HERE, "package", "ProviderAssets")

loader = importlib.machinery.SourceFileLoader("cub", APP)
spec = importlib.util.spec_from_loader("cub", loader)
cub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cub)
cub.import_dependencies()
Image, ImageDraw, ImageFont = cub.Image, cub.ImageDraw, cub.ImageFont

ORANGE = (201, 100, 66, 255)
INK = (240, 238, 235, 255)
BG_DARK = (32, 32, 32, 255)
GREEN = "#3FB950"
AMBER = "#D29922"
RED = "#F85149"


def font(px, bold=True):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(os.path.join(cub.FONT_DIR, name), px)
        except Exception:
            continue
    return ImageFont.load_default()


def logo(size):
    """Rounded Claude-orange tile with a usage pill across it."""
    ss = 8
    img = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size * ss
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=ORANGE)
    pad = s * 0.16
    bar_h = s * 0.20
    top = s * 0.5 - bar_h / 2
    d.rounded_rectangle((pad, top, s - pad, top + bar_h), radius=bar_h / 2,
                        fill=(0, 0, 0, 90))
    d.rounded_rectangle((pad, top, pad + (s - 2 * pad) * 0.72, top + bar_h),
                        radius=bar_h / 2, fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def widget_icon(size=128):
    """The board/picker icon: the tile plus the skull, so a spent limit reads
    the same here as it does in the tray."""
    img = logo(size).convert("RGBA")
    box = (size * 0.28, size * 0.10, size * 0.72, size * 0.54)
    cub.draw_skull(img, box, INK, False)
    return img


def bar(d, x, y, w, h, pct, color):
    d.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=(58, 58, 58, 255))
    span = max(w * pct / 100.0, h)
    d.rounded_rectangle((x, y, x + span, y + h), radius=h / 2, fill=color)


def screenshot(w=480, h=320):
    """A mock of the medium widget, for the widget picker."""
    img = Image.new("RGBA", (w, h), BG_DARK)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 16, w - 16, h - 16), radius=18, fill=(42, 42, 42, 255))

    icon = widget_icon(40)
    img.alpha_composite(icon, (36, 38))
    d.text((88, 46), "Claude usage", font=font(20), fill=INK)
    d.text((w - 130, 50), "updated 14:07", font=font(13, False), fill=(150, 150, 150, 255))

    rows = [("Session (5h)", 86, RED, "resets 16:20 · in 1h 12m"),
            ("Weekly", 41, GREEN, "resets Mon 09:00 · in 3d 4h"),
            ("Weekly (Opus)", 63, AMBER, "resets Mon 09:00 · in 3d 4h")]
    y = 104
    for label, pct, color, reset in rows:
        d.text((36, y), label, font=font(16), fill=INK)
        d.text((w - 36 - d.textlength("%d%%" % pct, font=font(16)), y), "%d%%" % pct,
               font=font(16), fill=color)
        bar(d, 36, y + 26, w - 72, 10, pct, color)
        d.text((36, y + 42), reset, font=font(12, False), fill=(150, 150, 150, 255))
        y += 70

    return img


def main():
    os.makedirs(IMAGES, exist_ok=True)
    os.makedirs(ASSETS, exist_ok=True)
    for name, size in (("StoreLogo.png", 50), ("Square44x44Logo.png", 44),
                       ("Square150x150Logo.png", 150)):
        logo(size).save(os.path.join(IMAGES, name))
    widget_icon(128).save(os.path.join(ASSETS, "ClaudeUsage_Icon.png"))
    screenshot().save(os.path.join(ASSETS, "ClaudeUsage_Screenshot.png"))
    print("assets written to", os.path.join(HERE, "package"))


if __name__ == "__main__":
    main()
