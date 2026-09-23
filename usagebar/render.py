"""Drawing with Pillow: the tray icons, the skull, the sparkle, fades."""

import os

from .deps import Image, ImageChops, ImageDraw, ImageFont
from .util import color_for, deep_merge, hex_to_rgba
from .win32 import theme_ink, windows_uses_light_theme


FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")


# ---------------------------------------------------------------------------
# Icon rendering
# ---------------------------------------------------------------------------

def load_font(cfg_icon, px):
    px = max(6, int(px))
    for name in (cfg_icon.get("font_file"), "segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
        if not name:
            continue
        for path in (os.path.join(FONT_DIR, name), name):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_text_centered(draw, box, text, font, fill, shadow):
    x0, y0, x1, y1 = box
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = x0 + (x1 - x0 - tw) / 2.0 - bbox[0]
    y = y0 + (y1 - y0 - th) / 2.0 - bbox[1]
    if shadow:
        off = max(1, int((y1 - y0) * 0.05))
        draw.text((x + off, y + off), text, font=font, fill=(0, 0, 0, 170))
    draw.text((x, y), text, font=font, fill=fill)


def draw_skull(img, box, fill, shadow):
    """A hand-drawn skull glyph, used in place of the number at 100%.

    Drawn on its own layer so the eye sockets, nose and tooth gaps can be
    punched straight through to whatever is behind it (the pill fill), which is
    what keeps it readable once it is downsampled to a 16px tray icon.
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    h = bh
    w = h * 0.86
    if w > bw:
        w, h = bw, bw / 0.86
    ox, oy = x0 + (bw - w) / 2.0, y0 + (bh - h) / 2.0
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    hole = (0, 0, 0, 0)

    def P(u, v):
        return (ox + u * w, oy + v * h)

    def ell(u0, v0, u1, v1, color):
        dl.ellipse([P(u0, v0), P(u1, v1)], fill=color)

    def rect(u0, v0, u1, v1, color, r=0.0):
        pts = [P(u0, v0), P(u1, v1)]
        if r:
            dl.rounded_rectangle(pts, radius=max(1, r * w), fill=color)
        else:
            dl.rectangle(pts, fill=color)

    # cranium + jaw
    ell(0.00, 0.00, 1.00, 0.82, fill)
    rect(0.24, 0.55, 0.76, 1.00, fill, r=0.14)
    # eye sockets
    ell(0.08, 0.24, 0.45, 0.60, hole)
    ell(0.55, 0.24, 0.92, 0.60, hole)
    # nose
    dl.polygon([P(0.50, 0.58), P(0.38, 0.75), P(0.62, 0.75)], fill=hole)
    # mouth line + tooth gaps
    rect(0.27, 0.80, 0.73, 0.855, hole)
    for u in (0.42, 0.58):
        rect(u - 0.03, 0.855, u + 0.03, 1.00, hole)

    if shadow:
        off = max(1, int(bh * 0.05))
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sh.paste(Image.new("RGBA", img.size, (0, 0, 0, 170)), (off, off), layer)
        img.alpha_composite(sh)
    img.alpha_composite(layer)


def render_strip(W, H, primary, secondary, cfg):
    """Draw the usage bar into a W x H box. Square (W == H) is one tray icon;
    wider strips get sliced across several adjacent tray icons."""
    ic = cfg["icon"]
    ss = max(1, int(ic.get("supersample", 8)))
    w, h = W * ss, H * ss
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pct = max(0.0, min(100.0, float(primary)))
    fill_col = hex_to_rgba(color_for(pct, cfg["thresholds"]))
    track_col = hex_to_rgba(ic["track_color"], int(ic.get("track_alpha", 200)))
    text_col = hex_to_rgba(ic["text_color"])
    margin = float(ic.get("margin", 1)) * ss
    radius = max(1, int(float(ic.get("corner_radius", 2)) * ss))
    style = ic.get("style", "bar_text")
    skull_at = ic.get("skull_at", 100)
    use_skull = skull_at is not None and pct >= float(skull_at) - 0.5
    skull_col = ic.get("skull_color", "auto")
    auto_skull = skull_col == "auto"
    skull_col = hex_to_rgba(theme_ink() if auto_skull else skull_col)
    # The drop shadow is there to lift pale glyphs off the taskbar; under a
    # near-black skull on a light taskbar it only smudges the outline.
    skull_shadow = ic.get("text_shadow", True) and not (
        auto_skull and windows_uses_light_theme())
    shown = round(pct) if pct < 99.5 else 99
    try:
        label = str(ic.get("label_format", "{pct}")).format(
            pct=shown, secondary=round(float(secondary or 0)))
    except Exception:
        label = "%d" % shown

    def glyph(box, font, color):
        """The number, or the skull once the limit is spent."""
        if use_skull:
            draw_skull(img, box, skull_col, skull_shadow)
        else:
            draw_text_centered(d, box, label, font, color, ic.get("text_shadow", True))

    def rrect(box, color):
        box = (box[0], box[1], max(box[0] + 1, box[2]), max(box[1] + 1, box[3]))
        try:
            d.rounded_rectangle(box, radius=radius, fill=color)
        except Exception:
            d.rectangle(box, fill=color)

    def hbar(y0, y1, value, color):
        rrect((margin, y0, w - margin, y1), track_col)
        span = (w - 2 * margin) * (max(0.0, min(100.0, float(value))) / 100.0)
        if span > 0:
            rrect((margin, y0, margin + max(span, radius * 1.2), y1), color)

    if style == "ring":
        size = min(w, h)
        th = float(ic.get("ring_thickness", 0.22)) * size
        cx, cy = w / 2.0, h / 2.0
        r = size / 2.0 - margin - th / 2
        box = (cx - r, cy - r, cx + r, cy + r)
        d.arc(box, 0, 360, fill=track_col, width=int(th))
        if pct > 0:
            d.arc(box, -90, -90 + 360 * pct / 100.0, fill=fill_col, width=int(th))
        font = load_font(ic, size * float(ic.get("font_scale", 0.78)) * 0.58)
        glyph((margin, margin, w - margin, h - margin), font, text_col)

    elif style == "text":
        font = load_font(ic, (h - 2 * margin) * float(ic.get("font_scale", 0.78)) * 1.25)
        glyph((margin, margin, w - margin, h - margin), font, fill_col)

    elif style == "bar":
        if ic.get("orientation") == "vertical" and W == H:
            rrect((margin, margin, w - margin, h - margin), track_col)
            fh = (h - 2 * margin) * pct / 100.0
            if fh > 0:
                rrect((margin, h - margin - fh, w - margin, h - margin), fill_col)
        else:
            th = float(ic.get("bar_thickness", 0.30)) * h
            hbar((h - th) / 2, (h + th) / 2, pct, fill_col)

    elif style == "text_bar":
        gap = float(ic.get("gap", 0.08)) * h
        bar_th = float(ic.get("bar_thickness", 0.30)) * h
        sec_th = float(ic.get("secondary_thickness", 0.14)) * h
        bottom = h - margin
        if bool(ic.get("show_secondary", True)) and secondary is not None:
            hbar(bottom - sec_th, bottom, secondary, hex_to_rgba(ic.get("secondary_color", "#7A7AFF")))
            bottom -= sec_th + gap * 0.5
        hbar(bottom - bar_th, bottom, pct, fill_col)
        text_area = (0, 0, w, bottom - bar_th - gap * 0.2)
        font = load_font(ic, (text_area[3] - text_area[1]) * 1.05)
        glyph(text_area, font, text_col)

    else:  # bar_text (default): number over a fill pill
        bottom = h - margin
        if bool(ic.get("show_secondary", True)) and secondary is not None:
            sec_th = float(ic.get("secondary_thickness", 0.14)) * h
            hbar(bottom - sec_th, bottom, secondary, hex_to_rgba(ic.get("secondary_color", "#7A7AFF")))
            bottom -= sec_th + float(ic.get("gap", 0.08)) * h * 0.5
        rrect((margin, margin, w - margin, bottom), track_col)
        span = (w - 2 * margin) * pct / 100.0
        if span > 0:
            rrect((margin, margin, margin + max(span, radius * 1.2), bottom), fill_col)
        font = load_font(ic, (bottom - margin) * float(ic.get("font_scale", 0.78)))
        glyph((margin, margin, w - margin, bottom), font, text_col)

    if ss > 1:
        img = img.resize((W, H), Image.LANCZOS)
    return img


def render_icon(size, primary, secondary, cfg):
    return render_strip(size, size, primary, secondary, cfg)


def segment_roles(cfg):
    """Role per tray icon, left to right. 'cells' mode uses `segments`;
    'slice' mode cuts one continuous bar into `count` pieces."""
    ic = cfg["icon"]
    if str(ic.get("segment_mode", "cells")) == "slice":
        return ["slice"] * max(1, min(8, int(ic.get("count", 1) or 1)))
    roles = list(ic.get("segments") or ["text"])
    n = int(ic.get("count", 0) or 0)
    if n:                       # `count` wins: pad with bars / truncate
        roles = (roles + ["bar"] * n)[:n]
    return roles[:8] or ["text"]


def render_cell(size, role, bar_index, bar_total, primary, secondary, cfg):
    """One self-contained tray icon in 'cells' mode."""
    ic = cfg["icon"]
    pct = max(0.0, min(100.0, float(primary)))
    col = color_for(pct, cfg["thresholds"])

    if role == "text":
        sub = deep_merge(cfg, {"icon": {"style": ic.get("text_cell_style", "text"),
                                        "show_secondary": ic.get("show_secondary", True)}})
        return render_strip(size, size, pct, secondary, sub)

    if role == "ring":
        return render_strip(size, size, pct, secondary, deep_merge(cfg, {"icon": {"style": "ring"}}))

    if role == "blank":
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # bar / secondary: this cell owns a slice of the 0-100 range
    if role == "secondary":
        value, cell_col = float(secondary or 0), ic.get("secondary_color", "#7A7AFF")
        share = 100.0
        low = 0.0
    else:
        value, cell_col = pct, col
        share = 100.0 / max(1, bar_total)
        low = bar_index * share
    local = max(0.0, min(share, value - low)) / share * 100.0

    sub = deep_merge(cfg, {"icon": {
        "style": "bar",
        "orientation": "horizontal",
        "show_secondary": False,
        "bar_thickness": ic.get("cell_bar_thickness", ic.get("bar_thickness", 0.30)),
    }})
    sub["thresholds"] = [{"at": 0, "color": cell_col}]
    return render_strip(size, size, local, None, sub)


def draw_sparkle(img, w, h, color):
    """A small four-point star in the widget's top-right corner: something
    special is on, open the flyout. Drawn at 4x and scaled down, like the rest."""
    size = max(7, int(h * 0.30))
    ss = 4
    S = size * ss
    star = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(star)
    c, t = S / 2.0, S * 0.13
    d.polygon([(c, 0), (c + t, c - t), (S, c), (c + t, c + t),
               (c, S), (c - t, c + t), (0, c), (c - t, c - t)], fill=color)
    star = star.resize((size, size), Image.LANCZOS)
    img.alpha_composite(star, (max(0, w - size - 1), 1))


def fade_image(image, factor):
    """Scale an image's alpha - used to dim numbers that are no longer live.

    Anything that was visible at all stays at alpha 1 or more: the widget's
    whole rectangle is covered by an alpha-1 veil so that clicks land on it, and
    rounding that veil down to zero would make everything but the glyphs
    click-through.
    """
    r, g, b, a = image.split()
    return Image.merge("RGBA", (r, g, b,
                                a.point(lambda v: max(1, int(round(v * factor))) if v else 0)))


def premultiply(image):
    """UpdateLayeredWindow wants premultiplied alpha; Pillow gives us straight."""
    r, g, b, a = image.split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a),
                                ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))
