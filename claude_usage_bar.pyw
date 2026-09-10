"""Claude usage bar - Windows 11 tray indicator.

Renders a live usage bar as a system-tray icon (right side of the taskbar,
next to the clock) driven by the same data Claude Code /usage shows.
Everything visual lives in config.json; edit it and pick "Reload config".

Run with pythonw.exe (no console). Log: claude_usage_bar.log next to this file.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
import winreg
from datetime import datetime, timezone

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "claude_usage_bar.log")
FALLBACK_LOG = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", "."),
                            "claude-usage-bar", "claude_usage_bar.log")
ICON_CACHE = os.path.join(APP_DIR, ".icons")
CRED_PATH = os.path.expanduser(os.path.join("~", ".claude", ".credentials.json"))
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")

DEFAULT_CONFIG = {
    "refresh_seconds": 60,
    "primary_metric": "session",
    "secondary_metric": "weekly_all",
    "icon": {
        "style": "bar_text",
        "count": 1,
        "segment_mode": "cells",
        "use_guid": True,
        "segments": ["text", "bar", "bar"],
        "reverse_segments": False,
        "label_format": "{pct}",
        "orientation": "horizontal",
        "margin": 1,
        "corner_radius": 2,
        "track_color": "#4A4A4A",
        "track_alpha": 200,
        "text_color": "#FFFFFF",
        "text_shadow": True,
        "font_file": "segoeuib.ttf",
        "font_scale": 0.78,
        "bar_thickness": 0.30,
        "ring_thickness": 0.22,
        "show_secondary": True,
        "secondary_thickness": 0.14,
        "secondary_color": "#7A7AFF",
        "gap": 0.08,
        "supersample": 8,
        "skull_at": 100,
    },
    "thresholds": [
        {"at": 0, "color": "#3FB950"},
        {"at": 60, "color": "#D29922"},
        {"at": 85, "color": "#F85149"},
    ],
    "tooltip_template": "Claude \u00b7 {primary_label}: {primary}%\n{secondary_label}: {secondary}%\nResets {primary_reset_short} (in {primary_reset_in})",
    "notifications": {"enabled": True, "at": [80, 95], "metric": "session"},
    "flyout": {
        "width": 340,
        "background": "#1E1E1E",
        "foreground": "#E6E6E6",
        "muted": "#9A9A9A",
        "accent": "#C96442",
        "track_color": "#3A3A3A",
        "font_family": "Segoe UI",
        "font_size": 9,
        "title_size": 11,
        "corner_offset": [12, 12],
        "close_on_focus_loss": True,
    },
    "tray": {"enabled": True},
    "taskbar_widget": {
        "enabled": False,
        "metric": None,
        "corner": "left",
        "width": 128,
        "offset": [8, 0],
        "padding": 5,
        "background": "auto",
        "text_color": "auto",
        "muted_color": "#9BA0A6",
        "font_file": "segoeui.ttf",
        "bold_font_file": "segoeuib.ttf",
        "show_bar": True,
        "show_reset": True,
        "bar_height": 4,
        "track_color": "auto",
        "hide_on_fullscreen": True,
        "supersample": 3,
    },
    "left_click": "flyout",
    "usage_page_url": "https://claude.ai/settings/usage",
}


def log(msg):
    line = "%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    for path in (LOG_PATH, FALLBACK_LOG):
        try:
            if path is FALLBACK_LOG:
                os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            return
        except Exception:
            continue


def import_dependencies(attempts=20, delay=3.0):
    """Import the third-party deps, retrying for a minute.

    At logon these can fail for reasons that clear themselves seconds later
    (roaming profile / OneDrive still mounting, antivirus holding site-packages).
    As a plain module-level import that was a silent death with nothing in the
    log; here every failure is at least recorded.
    """
    global requests, Image, ImageDraw, ImageFont, ImageTk
    last = ""
    for attempt in range(attempts):
        try:
            import requests as _requests
            from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
            from PIL import ImageTk as _ImageTk
        except Exception as exc:
            last = repr(exc)
            time.sleep(delay)
            continue
        requests, Image, ImageDraw, ImageFont = _requests, _Image, _ImageDraw, _ImageFont
        ImageTk = _ImageTk
        if attempt:
            log("dependencies imported after %d retries" % attempt)
        return True
    log("fatal: cannot import dependencies: %s" % last)
    return False


def deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    cfg = DEFAULT_CONFIG
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = deep_merge(DEFAULT_CONFIG, json.load(fh))
        else:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(DEFAULT_CONFIG, fh, indent=2)
    except Exception as exc:
        log("config load failed: %r" % (exc,))
    return cfg


def hex_to_rgba(value, alpha=255):
    v = str(value).lstrip("#")
    if len(v) == 8:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), int(v[6:8], 16))
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), alpha)


def color_for(pct, thresholds):
    chosen = thresholds[0]["color"]
    for t in sorted(thresholds, key=lambda x: x["at"]):
        if pct >= t["at"]:
            chosen = t["color"]
    return chosen


# ---------------------------------------------------------------------------
# Usage data
# ---------------------------------------------------------------------------

LABELS = {
    "session": "Session (5h)",
    "weekly_all": "Weekly (all models)",
    "weekly_scoped": "Weekly (scoped)",
    "weekly_opus": "Weekly (Opus)",
}


class Usage(object):
    def __init__(self):
        self.limits = []
        self.spend = None
        self.extra = None
        self.error = None
        self.updated = None
        self.raw = None

    def by_key(self, key):
        if key == "max" and self.limits:
            return max(self.limits, key=lambda l: l["percent"])
        for lim in self.limits:
            if lim["key"] == key:
                return lim
        return None

    def percent(self, key):
        lim = self.by_key(key)
        return lim["percent"] if lim else 0.0


def read_token():
    with open(CRED_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for section in ("claudeAiOauth", "claudeAiOAuth"):
        blob = data.get(section) or {}
        if blob.get("accessToken"):
            return blob["accessToken"]
    raise RuntimeError("no accessToken in credentials file")


def fetch_usage():
    u = Usage()
    try:
        token = read_token()
        resp = requests.get(
            USAGE_URL,
            headers={
                "Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
                "User-Agent": "claude-usage-bar/1.0",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            u.error = "auth" if resp.status_code in (401, 403) else "HTTP %s" % resp.status_code
            log("usage http %s" % resp.status_code)
            return u
        data = resp.json()
        u.raw = data
        for item in data.get("limits") or []:
            kind = item.get("kind") or "unknown"
            label = LABELS.get(kind, kind.replace("_", " ").title())
            scope = item.get("scope") or {}
            model = (scope.get("model") or {}).get("display_name")
            if model:
                label = "Weekly (%s)" % model
            u.limits.append({
                "key": kind,
                "label": label,
                "percent": float(item.get("percent") or 0),
                "resets_at": item.get("resets_at"),
                "severity": item.get("severity") or "normal",
                "group": item.get("group") or kind,
            })
        if not u.limits:
            for key, src in (("session", "five_hour"), ("weekly_all", "seven_day")):
                blob = data.get(src) or {}
                if blob.get("utilization") is not None:
                    u.limits.append({
                        "key": key,
                        "label": LABELS[key],
                        "percent": float(blob["utilization"]),
                        "resets_at": blob.get("resets_at"),
                        "severity": "normal",
                        "group": key,
                    })
        u.spend = data.get("spend")
        u.extra = data.get("extra_usage")
        u.updated = datetime.now()
    except FileNotFoundError:
        u.error = "not logged in"
    except requests.RequestException:
        u.error = "offline"
    except Exception:
        u.error = "error"
        log("fetch failed: %s" % traceback.format_exc())
    return u


def parse_reset(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def human_delta(dt):
    if not dt:
        return "?"
    secs = (dt - datetime.now(timezone.utc).astimezone()).total_seconds()
    if secs <= 0:
        return "now"
    hours, rem = divmod(int(secs), 3600)
    mins = rem // 60
    if hours >= 24:
        return "%dd %dh" % (hours // 24, hours % 24)
    if hours:
        return "%dh %dm" % (hours, mins)
    return "%dm" % mins


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
    shown = round(pct) if pct < 99.5 else 99
    try:
        label = str(ic.get("label_format", "{pct}")).format(
            pct=shown, secondary=round(float(secondary or 0)))
    except Exception:
        label = "%d" % shown

    def glyph(box, font, color):
        """The number, or the skull once the limit is spent."""
        if use_skull:
            draw_skull(img, box, color, ic.get("text_shadow", True))
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


# ---------------------------------------------------------------------------
# Win32 tray plumbing
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
NIN_SELECT = WM_APP + 0
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIF_GUID = 0x20
NIF_SHOWTIP = 0x80
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
TPM_RIGHTALIGN, TPM_BOTTOMALIGN, TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0008, 0x0020, 0x0100, 0x0002
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0000, 0x0800, 0x0008
PM_REMOVE = 0x0001

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("d1", wt.DWORD), ("d2", wt.WORD), ("d3", wt.WORD), ("d4", ctypes.c_byte * 8)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", ctypes.c_wchar * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wt.UINT), ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", GUID), ("hBalloonIcon", wt.HANDLE),
    ]


user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                                   wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.CreatePopupMenu.restype = wt.HMENU
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, wt.LPVOID]
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_void_p, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATA)]

ICON_NAMESPACE = uuid.UUID("8f2b41c6-0d7a-4f19-9a3e-7c5e2b1d4a60")


def guid_for(index):
    g = GUID()
    ctypes.memmove(ctypes.byref(g), uuid.uuid5(ICON_NAMESPACE, "segment-%d" % index).bytes_le, 16)
    return g


CMD_DETAILS, CMD_REFRESH, CMD_CONFIG, CMD_RELOAD, CMD_WEB, CMD_STARTUP, CMD_LOG, CMD_QUIT = range(1, 9)


def startup_lnk_path():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", "Claude Usage Bar.lnk")


def toggle_startup():
    lnk = startup_lnk_path()
    if os.path.exists(lnk):
        os.remove(lnk)
        return False
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    script = os.path.join(APP_DIR, "claude_usage_bar.pyw")
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='\"%s\"';$s.WorkingDirectory='%s';"
        "$s.WindowStyle=7;$s.Description='Claude usage bar';$s.Save()"
        % (lnk, pyw, script, APP_DIR)
    )
    subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                   creationflags=0x08000000, check=False)
    return os.path.exists(lnk)


class TrayApp(object):
    def __init__(self):
        self.cfg = load_config()
        self.use_guid = bool(self.cfg["icon"].get("use_guid", True))
        self.cfg_mtime = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else 0
        self.usage = Usage()
        self.usage.error = "loading"
        self.hicons = []
        self.registered = 0
        self.use_guid = bool(self.cfg["icon"].get("use_guid", True))
        self.icon_slot = 0
        self.last_notified = {}
        self.flyout = None
        self.widget = None
        self.flyout_anchor = "right"
        self._fetching = False
        self._pending = None
        self._lock = threading.Lock()

        os.makedirs(ICON_CACHE, exist_ok=True)
        self.icon_size = user32.GetSystemMetrics(SM_CXSMICON) or 16

        self.hinst = kernel32.GetModuleHandleW(None)
        self.wndproc = WNDPROC(self._wndproc)
        wc = WNDCLASS()
        wc.lpfnWndProc = self.wndproc
        wc.hInstance = self.hinst
        wc.lpszClassName = "ClaudeUsageBarWnd"
        self.atom = user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "ClaudeUsageBarWnd", "Claude Usage Bar",
                                           0, 0, 0, 0, 0, None, None, self.hinst, None)
        self.wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._add_icon()
        self.sync_widget()

    # -- tray icon ---------------------------------------------------------
    def _nid(self, flags, uid=1):
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        if self.use_guid:
            nid.uFlags |= NIF_GUID
            nid.guidItem = guid_for(uid)
        return nid

    def _roles(self):
        return segment_roles(self.cfg)

    def _segment_count(self):
        return len(self._roles())

    def _make_hicons(self):
        """Render every tray icon for the current usage state."""
        primary = self.usage.percent(self.cfg["primary_metric"])
        sec_key = self.cfg.get("secondary_metric")
        secondary = self.usage.percent(sec_key) if sec_key else None
        size = max(16, self.icon_size)
        roles = self._roles()
        n = len(roles)
        reverse = bool(self.cfg["icon"].get("reverse_segments"))

        if roles[0] == "slice":
            strip = render_strip(size * n, size, primary, secondary, self.cfg)
            images = [strip.crop((i * size, 0, (i + 1) * size, size)) for i in range(n)]
        else:
            bar_total = sum(1 for r in roles if r == "bar") or 1
            images, seen = [], 0
            for role in roles:
                idx = seen
                if role == "bar":
                    seen += 1
                images.append(render_cell(size, role, idx, bar_total, primary, secondary, self.cfg))
        if reverse:
            images.reverse()

        self.icon_slot ^= 1
        old, self.hicons = self.hicons, []
        for k, img in enumerate(images):
            path = os.path.join(ICON_CACHE, "tray%d_%d.ico" % (self.icon_slot, k))
            img.save(path, format="ICO", sizes=[(size, size)])
            self.hicons.append(user32.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE))
        for handle in old:
            if handle:
                user32.DestroyIcon(handle)
        return self.hicons

    def _tooltip(self):
        u = self.usage
        if u.error:
            return "Claude usage: %s" % u.error
        p_key = self.cfg["primary_metric"]
        s_key = self.cfg.get("secondary_metric") or p_key
        p, s = u.by_key(p_key), u.by_key(s_key)
        p = p or {"label": p_key, "percent": 0, "resets_at": None}
        s = s or {"label": s_key, "percent": 0, "resets_at": None}
        pr, sr = parse_reset(p["resets_at"]), parse_reset(s["resets_at"])
        values = {
            "primary_label": p["label"], "primary": round(p["percent"]),
            "secondary_label": s["label"], "secondary": round(s["percent"]),
            "primary_reset_short": pr.strftime("%H:%M") if pr else "?",
            "primary_reset_in": human_delta(pr),
            "secondary_reset_short": sr.strftime("%a %H:%M") if sr else "?",
            "secondary_reset_in": human_delta(sr),
            "updated": u.updated.strftime("%H:%M") if u.updated else "-",
        }
        try:
            text = self.cfg["tooltip_template"].format(**values)
        except Exception:
            text = "Claude %s%%" % values["primary"]
        return text[:127]

    def tray_enabled(self):
        return bool((self.cfg.get("tray") or {}).get("enabled", True))

    def _add_icon(self):
        if not self.tray_enabled():
            self.registered = self._segment_count()   # nothing to keep alive
            return True
        tip = self._tooltip()
        icons = self._make_hicons()
        added = 0
        for k, handle in enumerate(icons):
            for attempt in (0, 1):
                nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP, k + 1)
                nid.hIcon = handle
                nid.szTip = tip
                if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                    added += 1
                    break
                if attempt == 0 and self.use_guid:
                    # a previous run may still own the GUID; drop it and retry
                    stale = self._nid(0, k + 1)
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(stale))
                else:
                    log("add icon %d failed" % (k + 1))
        self.registered = added
        return added == len(icons)

    def ensure_icons(self):
        """Right after logon Explorer often refuses tray icons for a while;
        keep re-adding until they all stick."""
        if not self.tray_enabled():
            return
        want = self._segment_count()
        if self.registered >= want:
            return
        self.remove_icon()
        if self._add_icon():
            log("tray icons registered (%d)" % want)

    def update_icon(self):
        if self.widget is not None:
            self.widget.refresh(self.usage)
        if not self.tray_enabled():
            return
        if self.registered != self._segment_count():
            self.remove_icon()
            self._add_icon()
            return
        tip = self._tooltip()
        for k, handle in enumerate(self._make_hicons()):
            nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP, k + 1)
            nid.hIcon = handle
            nid.szTip = tip
            if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
                self.remove_icon()
                self._add_icon()
                return

    def notify(self, title, body):
        if not self.tray_enabled():
            return
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = body[:255]
        nid.dwInfoFlags = 0x01
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove_icon(self):
        for k in range(max(self.registered, self._segment_count(), 1)):
            nid = self._nid(0, k + 1)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.registered = 0

    # -- menu --------------------------------------------------------------
    def show_menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, CMD_DETAILS, "Show details")
        user32.AppendMenuW(menu, MF_STRING, CMD_REFRESH, "Refresh now")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_CONFIG, "Edit config...")
        user32.AppendMenuW(menu, MF_STRING, CMD_RELOAD, "Reload config")
        user32.AppendMenuW(menu, MF_STRING, CMD_WEB, "Open usage page")
        user32.AppendMenuW(menu, MF_STRING, CMD_LOG, "Open log")
        flags = MF_STRING | (MF_CHECKED if os.path.exists(startup_lnk_path()) else 0)
        user32.AppendMenuW(menu, flags, CMD_STARTUP, "Start with Windows")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "Quit")

        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0x0000, 0, 0)
        user32.DestroyMenu(menu)
        if cmd:
            self.on_command(cmd)

    def on_command(self, cmd):
        if cmd == CMD_DETAILS:
            self.toggle_flyout()
        elif cmd == CMD_REFRESH:
            self.start_fetch()
        elif cmd == CMD_CONFIG:
            os.startfile(CONFIG_PATH)
        elif cmd == CMD_RELOAD:
            self.reload_config()
        elif cmd == CMD_WEB:
            webbrowser.open(self.cfg.get("usage_page_url"))
        elif cmd == CMD_LOG:
            if os.path.exists(LOG_PATH):
                os.startfile(LOG_PATH)
        elif cmd == CMD_STARTUP:
            on = toggle_startup()
            self.notify("Claude usage bar", "Start with Windows: %s" % ("on" if on else "off"))
        elif cmd == CMD_QUIT:
            self.quit()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, NIN_SELECT):
                action = self.cfg.get("left_click", "flyout")
                if action == "flyout":
                    self.toggle_flyout()
                elif action == "refresh":
                    self.start_fetch()
                elif action == "web":
                    webbrowser.open(self.cfg.get("usage_page_url"))
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.show_menu()
            return 0
        if msg == WM_COMMAND:
            self.on_command(wparam & 0xFFFF)
            return 0
        if msg == self.wm_taskbar_created:
            self.registered = 0
            self._add_icon()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # -- data --------------------------------------------------------------
    def start_fetch(self):
        if self._fetching:
            return
        self._fetching = True

        def worker():
            result = fetch_usage()
            with self._lock:
                self._pending = result
            self._fetching = False

        threading.Thread(target=worker, daemon=True).start()

    def apply_pending(self):
        with self._lock:
            result, self._pending = self._pending, None
        if result is None:
            return
        if result.error and result.error != "not logged in" and self.usage.limits:
            result.limits = self.usage.limits  # keep last good numbers on a blip
            result.spend, result.extra = self.usage.spend, self.usage.extra
            result.updated = self.usage.updated
        self.usage = result
        self.update_icon()
        self.check_notifications()
        if self.flyout is not None:
            self.flyout.refresh(self.usage)

    def check_notifications(self):
        """Balloon once per threshold per reset window - never twice for the
        same level, and never again for a level already passed."""
        n = self.cfg.get("notifications") or {}
        if not n.get("enabled") or self.usage.error:
            return
        key = n.get("metric", "session")
        lim = self.usage.by_key(key)
        if not lim:
            return
        pct = lim["percent"]
        reset = lim.get("resets_at") or ""
        crossed = [float(t) for t in (n.get("at") or []) if pct >= float(t)]
        level = max(crossed) if crossed else None

        prev_reset, prev_level = self.last_notified.get(key, (None, None))
        if reset != prev_reset:         # the window rolled over: start fresh
            prev_level = None
        if level is None or (prev_level is not None and level <= prev_level):
            self.last_notified[key] = (reset, prev_level)
            return
        self.notify("Claude usage %d%%" % round(pct),
                    "%s at %d%% - resets in %s"
                    % (lim["label"], round(pct), human_delta(parse_reset(reset))))
        self.last_notified[key] = (reset, level)

    def reload_config(self):
        self.cfg = load_config()
        self.cfg_mtime = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else 0
        self.icon_size = user32.GetSystemMetrics(SM_CXSMICON) or 16
        if self.flyout is not None:
            self.flyout.destroy()
            self.flyout = None
        if not self.tray_enabled():
            self.remove_icon()
        self.sync_widget()
        self.update_icon()

    def sync_widget(self):
        """Create or drop the taskbar overlay to match the config."""
        want = bool((self.cfg.get("taskbar_widget") or {}).get("enabled"))
        if want and self.widget is None:
            self.widget = TaskbarWidget(self)
            log("taskbar overlay on")
        elif not want and self.widget is not None:
            self.widget.destroy()
            self.widget = None
            self.flyout_anchor = "right"
            log("taskbar overlay off")
        elif self.widget is not None:
            self.widget.reconfigure()

    def config_changed_on_disk(self):
        try:
            m = os.path.getmtime(CONFIG_PATH)
        except OSError:
            return False
        if m != self.cfg_mtime:
            self.cfg_mtime = m
            return True
        return False

    # -- flyout / lifecycle ------------------------------------------------
    def toggle_flyout(self):
        if self.flyout is not None and self.flyout.visible:
            self.flyout.hide()
            return
        if self.flyout is None:
            self.flyout = Flyout(self)
        self.flyout.show(self.usage)

    def quit(self):
        if self.widget is not None:
            self.widget.destroy()
        self.remove_icon()
        for handle in self.hicons:
            if handle:
                user32.DestroyIcon(handle)
        try:
            root.quit()
        except Exception:
            pass
        os._exit(0)


# ---------------------------------------------------------------------------
# Flyout window (tkinter)
# ---------------------------------------------------------------------------

import tkinter as tk


class Flyout(object):
    def __init__(self, app):
        self.app = app
        self.visible = False
        f = app.cfg["flyout"]
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=f["accent"])
        self.body = tk.Frame(self.win, bg=f["background"], padx=14, pady=12)
        self.body.pack(padx=1, pady=1, fill="both", expand=True)
        self.win.bind("<Escape>", lambda e: self.hide())
        if f.get("close_on_focus_loss", True):
            self.win.bind("<FocusOut>", lambda e: self.hide())

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()

    def refresh(self, usage):
        if not self.visible:
            return
        self.render(usage)

    def render(self, usage):
        f = self.app.cfg["flyout"]
        fam, fs, ts = f["font_family"], f["font_size"], f["title_size"]
        self._clear()
        width = int(f["width"])

        header = tk.Frame(self.body, bg=f["background"])
        header.pack(fill="x")
        tk.Label(header, text="Claude usage", bg=f["background"], fg=f["foreground"],
                 font=(fam, ts, "bold")).pack(side="left")
        stamp = usage.updated.strftime("%H:%M:%S") if usage.updated else "-"
        tk.Label(header, text=stamp, bg=f["background"], fg=f["muted"],
                 font=(fam, fs)).pack(side="right")

        if usage.error:
            tk.Label(self.body, text="Status: %s" % usage.error, bg=f["background"],
                     fg="#F85149", font=(fam, fs)).pack(anchor="w", pady=(8, 0))

        for lim in usage.limits:
            pct = lim["percent"]
            col = color_for(pct, self.app.cfg["thresholds"])
            row = tk.Frame(self.body, bg=f["background"])
            row.pack(fill="x", pady=(10, 0))
            tk.Label(row, text=lim["label"], bg=f["background"], fg=f["foreground"],
                     font=(fam, fs)).pack(side="left")
            tk.Label(row, text="%d%%" % round(pct), bg=f["background"], fg=col,
                     font=(fam, fs, "bold")).pack(side="right")
            cv = tk.Canvas(self.body, width=width, height=8, bg=f["track_color"],
                           highlightthickness=0, bd=0)
            cv.pack(fill="x", pady=(4, 0))
            cv.create_rectangle(0, 0, width * min(pct, 100) / 100.0, 8, fill=col, outline="")
            reset = parse_reset(lim["resets_at"])
            if reset:
                tk.Label(self.body,
                         text="resets %s  (in %s)" % (reset.strftime("%a %H:%M"), human_delta(reset)),
                         bg=f["background"], fg=f["muted"], font=(fam, fs - 1)).pack(anchor="w")

        extra = usage.extra or {}
        if extra.get("is_enabled"):
            tk.Label(self.body, text="Extra usage: %s%% of monthly limit"
                     % round(float(extra.get("utilization") or 0)),
                     bg=f["background"], fg=f["muted"], font=(fam, fs)).pack(anchor="w", pady=(10, 0))

        footer = tk.Frame(self.body, bg=f["background"])
        footer.pack(fill="x", pady=(14, 0))
        for text, cmd in (("Refresh", self.app.start_fetch),
                          ("Config", lambda: os.startfile(CONFIG_PATH)),
                          ("Close", self.hide)):
            tk.Label(footer, text=text, bg=f["background"], fg=f["accent"],
                     font=(fam, fs), cursor="hand2").pack(side="left", padx=(0, 14))
            footer.winfo_children()[-1].bind("<Button-1>", lambda e, c=cmd: c())

    def show(self, usage):
        f = self.app.cfg["flyout"]
        self.render(usage)
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        h = self.win.winfo_reqheight()
        # bottom-right, above the taskbar, using the work area
        rect = wt.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        ox, oy = f.get("corner_offset", [12, 12])
        if getattr(self.app, "flyout_anchor", "right") == "left":
            x = rect.left + int(ox)
        else:
            x = rect.right - w - int(ox)
        y = rect.bottom - h - int(oy)
        self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.visible = True

    def hide(self):
        self.win.withdraw()
        self.visible = False


# ---------------------------------------------------------------------------
# Taskbar overlay
#
# Windows 11 dropped deskbands, and the widgets board can't put anything on the
# taskbar itself, so the only way to be permanently visible down there is to
# draw our own window over the taskbar's free corner - the same trick the
# now-playing widgets use. It has to follow the taskbar: position, DPI,
# auto-hide, Explorer restarts, and it must get out of the way of fullscreen
# apps.
# ---------------------------------------------------------------------------

ABM_GETSTATE = 0x00000004
ABM_GETTASKBARPOS = 0x00000005
ABS_AUTOHIDE = 0x00000001
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
MONITOR_DEFAULTTONEAREST = 2


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD)]


shell32.SHAppBarMessage.restype = ctypes.c_size_t
shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(APPBARDATA)]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetForegroundWindow.restype = wt.HWND
user32.MonitorFromWindow.restype = wt.HANDLE
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
if hasattr(user32, "GetWindowLongPtrW"):
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


def taskbar_info():
    """(rect, edge, autohidden) for the taskbar, or None if it can't be found."""
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    if not shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(data)):
        return None
    state = shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(data))
    return data.rc, data.uEdge, bool(state & ABS_AUTOHIDE)


def foreground_is_fullscreen():
    """True when the focused window covers its whole monitor - games, video,
    slideshows. The shell itself doesn't count."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Shell_TrayWnd", "Progman", "WorkerW", "Windows.UI.Core.CoreWindow"):
        return False
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return (rect.left <= m.left and rect.top <= m.top
            and rect.right >= m.right and rect.bottom >= m.bottom)


gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.GetPixel.restype = wt.DWORD
gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

CLR_INVALID = 0xFFFFFFFF


def blend(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def sample_taskbar_color(rect, x_from, x_to):
    """The taskbar is translucent, so its colour depends on the wallpaper and
    the accent colour. Reading it off the screen beats guessing from the theme:
    take the most common pixel just beside where we sit."""
    hdc = user32.GetDC(None)
    if not hdc:
        return None
    try:
        y = (rect.top + rect.bottom) // 2
        counts = {}
        steps = 12
        for i in range(steps):
            x = int(x_from + (x_to - x_from) * i / float(steps))
            value = gdi32.GetPixel(hdc, x, y)
            if value == CLR_INVALID:
                continue
            counts[value] = counts.get(value, 0) + 1
        if not counts:
            return None
        best = max(counts.items(), key=lambda kv: kv[1])[0]
        return (best & 0xFF, (best >> 8) & 0xFF, (best >> 16) & 0xFF)
    finally:
        user32.ReleaseDC(None, hdc)


def windows_uses_light_theme():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except Exception:
        return False


class TaskbarWidget(object):
    """A small always-visible readout docked in the taskbar's free corner."""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.label = tk.Label(self.win, bd=0, highlightthickness=0)
        self.label.pack(fill="both", expand=True)
        # Only the label is bound: it fills the window, and binding both would
        # deliver every click twice (open the flyout, then close it again).
        self.label.bind("<Button-1>", self._on_left)
        self.label.bind("<Button-3>", self._on_right)
        self.label.configure(cursor="hand2")

        self.hwnd = None
        self.photo = None
        self.geometry = None
        self.shown = False
        self._last_key = None
        self._bg_sampled = None
        self._bg_at = 0.0
        self.reconfigure()

    # -- window plumbing ---------------------------------------------------
    def _apply_ex_styles(self):
        """No taskbar button, no alt-tab entry, and clicks must never steal
        focus from whatever the user is typing in."""
        self.win.update_idletasks()
        hwnd = self.win.winfo_id()
        parent = user32.GetParent(hwnd)
        self.hwnd = parent or hwnd
        get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = get(self.hwnd, GWL_EXSTYLE)
        put(self.hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

    def cfg(self):
        return self.app.cfg.get("taskbar_widget") or {}

    def reconfigure(self):
        self._last_key = None
        self.refresh(self.app.usage)

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _on_left(self, _event):
        action = self.app.cfg.get("left_click", "flyout")
        if action == "flyout":
            self.app.toggle_flyout()
        elif action == "refresh":
            self.app.start_fetch()
        elif action == "web":
            webbrowser.open(self.app.cfg.get("usage_page_url"))
        return "break"

    def _on_right(self, _event):
        self.app.show_menu()
        return "break"

    # -- placement ---------------------------------------------------------
    def _target_geometry(self):
        """Where to sit, or None when the taskbar is hidden or not horizontal."""
        info = taskbar_info()
        if info is None:
            return None
        rect, edge, autohide = info
        if edge not in (ABE_TOP, ABE_BOTTOM):
            return None            # vertical taskbar: no corner worth taking
        height = rect.bottom - rect.top
        if height <= 0 or rect.right <= rect.left:
            return None
        if autohide:
            # While hidden the taskbar parks itself just off the screen edge.
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            monitor = user32.MonitorFromWindow(user32.FindWindowW("Shell_TrayWnd", None),
                                               MONITOR_DEFAULTTONEAREST)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                m = mi.rcMonitor
                if rect.bottom <= m.top + 2 or rect.top >= m.bottom - 2:
                    return None

        c = self.cfg()
        scale = height / 48.0                      # 48px is the 100% DPI taskbar
        pad = max(2, int(float(c.get("padding", 5)) * scale))
        h = max(12, height - 2 * pad)
        w = max(40, int(float(c.get("width", 128)) * scale))
        ox, oy = (c.get("offset") or [8, 0])[:2]
        ox, oy = int(float(ox) * scale), int(float(oy) * scale)

        if str(c.get("corner", "left")) == "right":
            x = rect.right - w - ox
        else:
            x = rect.left + ox
        y = rect.top + (height - h) // 2 + oy
        return x, y, w, h

    def tick(self):
        """Called on a timer: keep position, visibility and Z-order honest."""
        if self.app.cfg.get("taskbar_widget", {}).get("hide_on_fullscreen", True) \
                and foreground_is_fullscreen():
            self._hide()
            return
        geometry = self._target_geometry()
        if geometry is None:
            self._hide()
            return
        if geometry != self.geometry:
            self.geometry = geometry
            x, y, w, h = geometry
            self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
            self._last_key = None            # re-render at the new size
            self.refresh(self.app.usage)
        self._show()
        if self.hwnd:
            # The taskbar is topmost too; keep re-asserting our place above it.
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _show(self):
        if not self.shown:
            self.win.deiconify()
            self.shown = True
            if self.hwnd is None:
                self._apply_ex_styles()
            self.app.flyout_anchor = str(self.cfg().get("corner", "left"))

    def _hide(self):
        if self.shown:
            self.win.withdraw()
            self.shown = False

    # -- drawing -----------------------------------------------------------
    def _taskbar_bg(self):
        """Cached sample of the taskbar colour next to the overlay."""
        now = time.time()
        if self._bg_sampled is not None and now - self._bg_at < 4.0:
            return self._bg_sampled
        self._bg_at = now
        info = taskbar_info()
        if info is not None and self.geometry is not None:
            rect = info[0]
            x, _, w, _ = self.geometry
            gap = max(24, w // 4)
            if str(self.cfg().get("corner", "left")) == "right":
                self._bg_sampled = sample_taskbar_color(rect, max(rect.left, x - gap * 2), x - 8)
            else:
                self._bg_sampled = sample_taskbar_color(rect, x + w + 8, min(rect.right - 2, x + w + gap * 2))
        return self._bg_sampled

    def _colors(self):
        c = self.cfg()
        light = windows_uses_light_theme()
        bg = c.get("background", "auto")
        fg = c.get("text_color", "auto")
        if bg == "auto":
            sampled = self._taskbar_bg()
            bg = "#%02X%02X%02X" % sampled if sampled else ("#F3F3F3" if light else "#1F1F1F")
        if fg == "auto":
            fg = "#1A1A1A" if light else "#F2F2F2"
        return bg, fg, c.get("muted_color", "#9BA0A6")

    def refresh(self, usage):
        if self.geometry is None:
            return
        c = self.cfg()
        key = c.get("metric") or self.app.cfg["primary_metric"]
        limit = usage.by_key(key)
        pct = float(limit["percent"]) if limit else 0.0
        reset = parse_reset(limit["resets_at"]) if limit else None
        state = (round(pct, 1), human_delta(reset), usage.error, self.geometry,
                 self._taskbar_bg(), windows_uses_light_theme())
        if state == self._last_key:
            return
        self._last_key = state

        _, _, w, h = self.geometry
        image = self._render(w, h, pct, reset, usage.error)
        self.photo = ImageTk.PhotoImage(image)
        bg = self._colors()[0]
        self.label.configure(image=self.photo, bg=bg)
        self.win.configure(bg=bg)

    def _render(self, w, h, pct, reset, error):
        c = self.cfg()
        bg, fg, muted = self._colors()
        ss = max(1, int(c.get("supersample", 3)))
        img = Image.new("RGB", (w * ss, h * ss), hex_to_rgba(bg)[:3])
        d = ImageDraw.Draw(img)
        W, H = w * ss, h * ss
        pct = max(0.0, min(100.0, pct))
        color = color_for(pct, self.app.cfg["thresholds"])
        spent = pct >= 99.5

        def font(px, bold=True):
            name = c.get("bold_font_file" if bold else "font_file",
                         "segoeuib.ttf" if bold else "segoeui.ttf")
            return load_font({"font_file": name}, px)

        if error:
            d.text((0, H / 2), " " + str(error)[:18], font=font(H * 0.42, False),
                   fill=hex_to_rgba(muted)[:3], anchor="lm")
            return img.resize((w, h), Image.LANCZOS)

        # Left: the number (or the skull, same as the tray at 100%).
        num_font = font(H * 0.62)
        if spent:
            skull_w = H * 0.52
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw_skull(layer, (0, H * 0.18, skull_w, H * 0.82), hex_to_rgba(color), False)
            img.paste(layer, (0, 0), layer)
            text_end = skull_w
        else:
            label = "%d%%" % round(pct)
            d.text((0, H / 2), label, font=num_font, fill=hex_to_rgba(color)[:3], anchor="lm")
            text_end = d.textlength(label, font=num_font)

        x0 = text_end + H * 0.22
        if x0 >= W:
            return img.resize((w, h), Image.LANCZOS)

        # Right: countdown over a slim bar, both inside the remaining width.
        show_reset = bool(c.get("show_reset", True)) and reset is not None
        show_bar = bool(c.get("show_bar", True))
        bar_h = float(c.get("bar_height", 4)) * ss * (h / 38.0)   # 38px tall at 100% DPI
        bar_h = int(max(2 * ss, min(H * 0.22, bar_h)))

        if show_reset:
            small = font(H * 0.36, False)
            text = human_delta(reset)
            while d.textlength(text, font=small) > (W - x0) and len(text) > 4:
                text = text[:-1]
            d.text((x0, H * 0.30), text, font=small, fill=hex_to_rgba(muted)[:3], anchor="lm")
            bar_y = H * 0.62
        else:
            bar_y = H / 2 - bar_h / 2

        if show_bar:
            track = c.get("track_color", "auto")
            if track == "auto":
                # A tint of the taskbar towards the text colour: readable on a
                # light taskbar and on a dark one without configuring anything.
                track = blend(hex_to_rgba(bg)[:3], hex_to_rgba(fg)[:3], 0.22)
            else:
                track = hex_to_rgba(track)[:3]
            d.rounded_rectangle((x0, bar_y, W - 1, bar_y + bar_h), radius=bar_h / 2, fill=track)
            span = (W - 1 - x0) * pct / 100.0
            if span > 0:
                d.rounded_rectangle((x0, bar_y, x0 + max(span, bar_h), bar_y + bar_h),
                                    radius=bar_h / 2, fill=hex_to_rgba(color)[:3])

        return img.resize((w, h), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Main loop: tkinter drives, Win32 messages are pumped from it
# ---------------------------------------------------------------------------

_instance_mutex = None


def single_instance():
    """True if we own the single-instance slot.

    The mutex lives in the per-session namespace (the Global one needs a
    privilege we may not have) and ERROR_ALREADY_EXISTS is only believed when a
    real instance window answers - otherwise a stale handle could keep the app
    from ever starting again.
    """
    global _instance_mutex
    ctypes.set_last_error(0)
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\ClaudeUsageBarMutex")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    return not user32.FindWindowW("ClaudeUsageBarWnd", None)


def main():
    global root
    log("starting (pid %d, %s)" % (os.getpid(), sys.executable))
    if not single_instance():
        log("another instance is running; exiting")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    root.withdraw()
    app = TrayApp()

    msg = wt.MSG()

    def pump():
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        app.apply_pending()
        root.after(40, pump)

    def tick():
        app.start_fetch()
        root.after(max(10, int(app.cfg["refresh_seconds"])) * 1000, tick)

    def watch_config():
        if app.config_changed_on_disk():
            app.reload_config()
        root.after(2000, watch_config)

    def clock():  # keep "resets in" fresh in the tooltip
        app.update_icon()
        root.after(30000, clock)

    def keep_icons():
        app.ensure_icons()
        root.after(5000, keep_icons)

    def keep_widget():
        if app.widget is not None:
            try:
                app.widget.tick()
            except Exception:
                log("overlay tick failed: %s" % traceback.format_exc())
        root.after(500, keep_widget)

    root.after(50, pump)
    root.after(100, tick)
    root.after(2000, watch_config)
    root.after(5000, keep_icons)
    root.after(300, keep_widget)
    root.after(30000, clock)
    root.mainloop()


if __name__ == "__main__":
    try:
        if import_dependencies():
            main()
    except Exception:
        log("fatal: %s" % traceback.format_exc())
        raise
