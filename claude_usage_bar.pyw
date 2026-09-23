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
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
import winreg
from datetime import datetime, timedelta, timezone

# Frozen into an .exe, __file__ points inside PyInstaller's temporary unpack
# directory; the folder the user actually put the app in is the executable's.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _writable(path):
    probe = os.path.join(path, ".write-probe")
    try:
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except Exception:
        return False


# Settings, log and cache live beside the app - which is what you want for a
# clone or an exe in a folder of your own. Somewhere read-only (Program Files,
# a network share) they fall back to the usual per-user location instead.
DATA_DIR = APP_DIR if _writable(APP_DIR) else os.path.join(
    os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", "."), "claude-usage-bar")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = APP_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "claude_usage_bar.log")
FALLBACK_LOG = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", "."),
                            "claude-usage-bar", "claude_usage_bar.log")
ICON_CACHE = os.path.join(DATA_DIR, ".icons")
USAGE_CACHE = os.path.join(DATA_DIR, ".usage_cache.json")
NOTIFY_STATE = os.path.join(DATA_DIR, ".notify_state.json")
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
        {"at": 0, "color": "#22C55E"},
        {"at": 60, "color": "#F59E0B"},
        {"at": 85, "color": "#EF4444"},
    ],
    "tooltip_template": "Claude \u00b7 {primary_label}: {primary}%\n{secondary_label}: {secondary}%\nResets {primary_reset_short} (in {primary_reset_in})",
    "notifications": {"enabled": True, "at": [80, 95, 100],
                      "metrics": ["session", "weekly_all", "weekly_scoped"]},
    # Ask the usage endpoint for promotional grants once an hour. The endpoint
    # only answers Claude Code, so that one request uses Claude Code's user
    # agent; set this to false to never do that.
    "check_events": True,
    "flyout": {
        "width": 320,
        "theme": "auto",
        "accent": "auto",
        "corner_offset": [12, 12],
        "close_on_focus_loss": True,
    },
    "tray": {"enabled": False},
    "taskbar_widget": {
        "enabled": True,
        "metric": None,
        "corner": "left",
        "width": 128,
        "offset": [8, 0],
        "padding": 5,
        "text_color": "auto",
        "muted_color": "auto",
        "font_file": "segoeui.ttf",
        "bold_font_file": "segoeuib.ttf",
        "show_bar": True,
        "show_reset": True,
        "bar_height": 4,
        "background": "transparent",
        "background_alpha": 255,
        "corner_radius": 6,
        "stale_opacity": 0.7,
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
    global requests, Image, ImageDraw, ImageFont, ImageTk, ImageChops
    last = ""
    for attempt in range(attempts):
        try:
            import requests as _requests
            from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
            from PIL import ImageTk as _ImageTk
            from PIL import ImageChops as _ImageChops
        except Exception as exc:
            last = repr(exc)
            time.sleep(delay)
            continue
        requests, Image, ImageDraw, ImageFont = _requests, _Image, _ImageDraw, _ImageFont
        ImageTk, ImageChops = _ImageTk, _ImageChops
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
                user = json.load(fh)
            cfg = deep_merge(DEFAULT_CONFIG, user)
            # The old single "metric" still means what it says: otherwise the
            # default "metrics" list is merged in and quietly wins over it.
            n = user.get("notifications") if isinstance(user, dict) else None
            if isinstance(n, dict) and "metric" in n and "metrics" not in n:
                cfg["notifications"] = dict(cfg["notifications"], metrics=[n["metric"]])
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


def shade(color, factor):
    """Lighten (factor > 0) or darken (factor < 0) a colour, keeping its hue."""
    r, g, b = hex_to_rgba(color)[:3]
    if factor >= 0:
        r, g, b = (int(c + (255 - c) * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c * (1 + factor)) for c in (r, g, b))
    return "#%02X%02X%02X" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


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
    status = None
    stale = False
    with_events = False

    def age_seconds(self):
        """How old the numbers are, or None when there are none."""
        if self.updated is None:
            return None
        return max(0.0, (datetime.now() - self.updated).total_seconds())

    def __init__(self):
        self.limits = []
        self.spend = None
        self.extra = None
        self.error = None
        self.updated = None
        self.raw = None
        self.breakdown = []     # where this week's usage went, by product
        self.buckets = []       # every other usage pool the endpoint reports
        self.events = None      # live grants; None = not checked yet
        self.events_checked = 0.0

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


def fetch_usage(with_events=False):
    """One poll. With `with_events`, the same request also asks for promotional
    grants - which the endpoint only reports to Claude Code, so that request
    identifies as the Claude Code installed here. It returns every usual field
    as well (only `spend` is skipped), so the event check costs no extra call."""
    u = Usage()
    u.with_events = with_events
    try:
        token = read_token()
        resp = requests.get(
            USAGE_URL,
            params={"cedar_ember": "1", "skip_spend": "1"} if with_events else None,
            headers={
                "Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
                "User-Agent": claude_code_user_agent() if with_events else "claude-usage-bar/1.0",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            u.status = resp.status_code
            u.error = {
                401: "Signed out - run any Claude Code command",
                403: "Signed out - run any Claude Code command",
                429: "Rate limited by the usage API",
                500: "Usage service is having trouble",
                503: "Usage service is having trouble",
            }.get(resp.status_code, "Usage API error (HTTP %s)" % resp.status_code)
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
                "active": bool(item.get("is_active")),
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
        u.breakdown = breakdown_rows(data)
        u.buckets = extra_buckets(data)
        if with_events:
            # Grants are a bonus on top of the usage numbers: if they cannot
            # be read, the numbers still count and events stays None (unknown).
            try:
                u.events = parse_events(data)
                u.events_checked = time.time()
            except Exception:
                log("events unreadable: %s" % traceback.format_exc())
        u.updated = datetime.now()
    except FileNotFoundError:
        u.error = "Not signed in to Claude Code"
    except requests.RequestException:
        u.error = "Offline"
    except Exception:
        u.error = "Something went wrong"
        log("fetch failed: %s" % traceback.format_exc())
    return u


def save_usage_cache(usage):
    try:
        with open(USAGE_CACHE, "w", encoding="utf-8") as fh:
            json.dump({"limits": usage.limits, "extra": usage.extra, "spend": usage.spend,
                       "breakdown": usage.breakdown, "buckets": usage.buckets,
                       "events": getattr(usage, "events", None),
                       "events_checked": getattr(usage, "events_checked", 0),
                       "updated": usage.updated.isoformat() if usage.updated else None}, fh)
    except Exception:
        pass


def load_usage_cache():
    """Show the last known numbers immediately at startup instead of a blank
    panel - a fresh poll can be a minute away, or rate limited."""
    u = Usage()
    try:
        with open(USAGE_CACHE, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        u.limits = blob.get("limits") or []
        u.extra, u.spend = blob.get("extra"), blob.get("spend")
        u.breakdown, u.buckets = blob.get("breakdown") or [], blob.get("buckets") or []
        u.events, u.events_checked = blob.get("events"), float(blob.get("events_checked") or 0)
        if blob.get("updated"):
            u.updated = datetime.fromisoformat(blob["updated"])
        if not u.limits:
            return None
        age = u.age_seconds()
        if age is None or age > 12 * 3600:
            return None          # a day-old percentage is not worth showing
        u.stale = True
        return u
    except Exception:
        return None


# The endpoint reports several usage pools as top-level objects shaped like
# {"utilization": .., "resets_at": .., "locked_reason": ..}. Most are
# codenamed and null. five_hour and seven_day are the pools `limits` already
# describes and extra_usage is the paid overage shown on its own; everything
# else is surfaced as-is, because the names change.
_LIMIT_BUCKETS = ("five_hour", "seven_day", "extra_usage")


def extra_buckets(data):
    out = []
    for key, value in (data or {}).items():
        if key in _LIMIT_BUCKETS or not isinstance(value, dict) or "utilization" not in value:
            continue
        promotional = "promo" in key.lower()
        # seven_day_<model> pools repeat the model-scoped weekly limits that
        # `limits` already lists by name.
        if key.startswith("seven_day_") and not promotional:
            continue
        try:
            pct = float(value.get("utilization") or 0)
        except (TypeError, ValueError):
            continue             # one odd pool must not cost the whole poll
        out.append({
            "key": key,
            "label": key.replace("_", " ").capitalize(),
            "percent": pct,
            "resets_at": value.get("resets_at"),
            "locked_reason": value.get("locked_reason"),
            "promotional": promotional,
        })
    return out


def breakdown_rows(data):
    """Where this week's usage went, by product - only the fields shown, so
    nothing else the endpoint puts in a row ends up in the cache."""
    blob = (data or {}).get("seven_day_breakdown")
    rows = blob.get("rows") if isinstance(blob, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            pct = float(row.get("percent") or 0)
        except (TypeError, ValueError):
            continue
        key = str(row.get("key") or "")
        out.append({"key": key, "display_name": str(row.get("display_name") or key or "?"),
                    "percent": pct})
    return out


def window_key(resets_at):
    """A stable name for one limit window.

    The API reports the same reset instant with different microseconds on
    every request ("...12:10:00.165535" then "...12:10:00.134418"), so the raw
    string cannot identify a window - comparing it made every poll look like a
    fresh window and re-announced the same threshold once a minute. Resets land
    on whole minutes; rounding to the minute absorbs the jitter.
    """
    if not resets_at:
        return ""
    moment = parse_reset(resets_at)
    if moment is None:
        return str(resets_at).split(".")[0]
    moment = moment.astimezone(timezone.utc) + timedelta(seconds=30)
    return moment.strftime("%Y-%m-%dT%H:%M")


def limit_identity(lim):
    """A stable name for one limit across polls. Several model-scoped weekly
    limits share the kind "weekly_scoped", so the model is part of the name."""
    if lim.get("key") == "weekly_scoped":
        return "weekly_scoped:%s" % lim.get("label", "")
    return lim.get("key") or "?"


def watched_limits(usage, metrics):
    """(identity, limit) for every limit the metric names select. "max" is
    resolved to whichever limit is highest right now, but keeps that limit's
    own identity - so the answer changing does not look like a new window."""
    seen, out = set(), []
    for metric in metrics:
        if metric == "max":
            chosen = [max(usage.limits, key=lambda l: l["percent"])] if usage.limits else []
        else:
            chosen = [l for l in usage.limits if l.get("key") == metric]
        for lim in chosen:
            identity = limit_identity(lim)
            if identity not in seen:
                seen.add(identity)
                out.append((identity, lim))
    return out


def load_notify_state(path):
    """What has already been announced, per limit: {key: [window, level]}.
    Kept on disk so a restart does not announce it all over again."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return {k: (v[0], v[1]) for k, v in raw.items() if isinstance(v, list) and len(v) == 2}
    except Exception:
        return {}


def save_notify_state(path, state):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({k: [v[0], v[1]] for k, v in state.items()}, fh)
    except Exception:
        pass


EVENT_CHECK_SECONDS = 3600       # grants change rarely; the endpoint rate-limits hard
USAGE_PAGE = "https://claude.ai/settings/usage"


def claude_code_user_agent():
    """The user agent of the installed Claude Code.

    The usage endpoint only reports promotional grants to Claude Code itself:
    asked by anything else it answers `"eligible": false, "ineligible_reason":
    "surface"`. So the one request that looks for grants identifies as the
    Claude Code on this machine - read from its install, not guessed.
    """
    version = None
    shape = re.compile(r"^\d+(\.\d+)+$")
    try:
        root = os.path.expanduser(os.path.join("~", ".local", "share", "claude", "versions"))
        names = [n for n in os.listdir(root) if shape.match(n)]
        if names:
            version = max(names, key=lambda n: tuple(int(x) for x in n.split(".")))
    except Exception:
        pass
    if not version:
        try:
            with open(os.path.expanduser(os.path.join("~", ".claude.json")), encoding="utf-8") as fh:
                seen = json.load(fh).get("lastReleaseNotesSeen")
            if isinstance(seen, str) and shape.match(seen):
                version = seen
        except Exception:
            pass
    return "claude-cli/%s (external, cli)" % (version or "2.1.0")


def _count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_events(data):
    """Grants that are live right now, from any program in the response.

    A program is any top-level object carrying a `grants` list (today that is
    `cedar_ember`, the limit-reset program). Reading them by shape rather than
    by codename means the next promotion shows up without a code change.
    """
    now = datetime.now(timezone.utc)
    events = []
    for program, value in (data or {}).items():
        if not isinstance(value, dict) or not isinstance(value.get("grants"), list):
            continue
        if value.get("eligible") is False:
            continue
        for grant in value["grants"]:
            if not isinstance(grant, dict):
                continue
            left = _count(grant.get("resets_left"))
            ends = parse_reset(grant.get("ends_at"))
            if grant.get("paused") or (left is not None and left <= 0):
                continue
            if ends is not None and ends <= now:
                continue
            clears = grant.get("clears")
            events.append({
                "program": program,
                "id": str(grant.get("id") or program),
                "label": str(grant.get("label") or program.replace("_", " ").capitalize()),
                "resets_left": left,
                "resets_total": _count(grant.get("resets_total")),
                "ends_at": grant.get("ends_at") if ends is not None else None,
                "clears": [str(c) for c in clears] if isinstance(clears, list) else [],
                "usable_now": grant.get("usable_now") is not False,
            })
    return events


def live_events(usage):
    """The cached grants that have not expired. The cache can outlive a grant
    when polls keep failing, so expiry is checked again at display time."""
    now = datetime.now(timezone.utc)
    out = []
    for event in (getattr(usage, "events", None) or []):
        ends = parse_reset(event.get("ends_at"))
        if ends is None or ends > now:
            out.append(event)
    return out


def describe_clears(clears):
    """What a limit reset puts back to full, in words."""
    names = {"five_hour": "5-hour", "seven_day": "weekly"}
    parts = [names[c] for c in clears if c in names]
    if not parts:
        return ""
    return " and ".join(parts) + (" limit" if len(parts) == 1 else " limits")


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
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
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
        self.usage = load_usage_cache() or Usage()
        if not self.usage.limits:
            self.usage.error = "Loading…"
        self.hicons = []
        self.registered = 0
        self.use_guid = bool(self.cfg["icon"].get("use_guid", True))
        self.icon_slot = 0
        self.state_path = NOTIFY_STATE
        self.last_notified = load_notify_state(self.state_path)
        self._signed_out_since = None
        self.events_retry_at = 0.0       # a failed event check waits its turn
        self._event_backoff = 0
        self.flyout = None
        self.widget = None
        self.toast = None
        self._fetching = False
        self._pending = None

        self.backoff = 0
        self.retry_at = 0
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
        """Balloon through the tray when there is one, our own toast when there
        isn't - the warnings must not depend on which surface is enabled.
        Returns False when nothing was shown, so the caller can try again."""
        if self.tray_enabled() and self.registered:
            nid = self._nid(NIF_INFO)
            nid.szInfoTitle = title[:63]
            nid.szInfo = body[:255]
            nid.dwInfoFlags = 0x01
            if shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
                return True
        try:
            if self.toast is None:
                self.toast = Toast(self)
            return self.toast.show(title, body)
        except Exception:
            log("toast failed: %s" % traceback.format_exc())
            return False

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
            self.start_fetch(manual=True)
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
                    self.start_fetch(manual=True)
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
    def events_enabled(self):
        return bool(self.cfg.get("check_events", True))

    def event_poll_due(self):
        """One poll an hour doubles as the event check, so looking for grants
        never costs a request of its own. A failed event check backs off on its
        own clock, and the polls in between go out as plain usage requests - so
        a problem with the event check can never freeze the numbers."""
        if not self.events_enabled():
            return False
        now = time.time()
        if now < self.events_retry_at:
            return False
        age = now - float(self.usage.events_checked or 0)
        return self.usage.events is None or age >= EVENT_CHECK_SECONDS

    def _event_check_failed(self, result):
        self._event_backoff = min(max(self._event_backoff * 2, 300), EVENT_CHECK_SECONDS)
        self.events_retry_at = time.time() + self._event_backoff
        log("event check failed (%s); next in %ds"
            % (result.error or "unreadable grants", self._event_backoff))

    def _back_off(self):
        self.backoff = min(max(self.backoff * 2, 120), 1800)
        self.retry_at = time.time() + self.backoff
        log("rate limited; next poll in %ds" % self.backoff)

    def note_rate_limit(self, result):
        """429 means the endpoint wants us to slow down; polling it every minute
        regardless just keeps it angry (and floods the log)."""
        if getattr(result, "status", None) == 429:
            self._back_off()
        elif not result.error:
            self.backoff = 0
            self.retry_at = 0

    def start_fetch(self, manual=False, events=None):
        if self._fetching:
            return
        if not manual and self.retry_at and time.time() < self.retry_at:
            return
        self._fetching = True
        if events is None:
            # Refresh re-checks events too, so a reset you have just used
            # stops being advertised straight away.
            events = self.events_enabled() and (manual or self.event_poll_due())

        def worker():
            result = fetch_usage(events)
            self._fetching = False          # before the result is visible, so
            with self._lock:                # apply_pending can fetch again
                self._pending = result

        threading.Thread(target=worker, daemon=True).start()

    def apply_pending(self):
        if self._pending is None:        # cheap check on the hot pump path
            return
        with self._lock:
            result, self._pending = self._pending, None
        if result is None:
            return
        if result.with_events:
            if result.error or result.events is None:
                self._event_check_failed(result)
                if result.status not in (None, 429):
                    # Refused as the event check: that says nothing about
                    # usage, so ask again the plain way rather than show - or
                    # announce - an error that may be the event check's alone.
                    self.start_fetch(manual=True, events=False)
                    return
            else:
                self._event_backoff = 0
        if result.error and result.error != "Not signed in to Claude Code" and self.usage.limits:
            result.limits = self.usage.limits  # keep last good numbers on a blip
            result.spend, result.extra = self.usage.spend, self.usage.extra
            result.breakdown, result.buckets = self.usage.breakdown, self.usage.buckets
            result.updated = self.usage.updated
        fresh_events = result.with_events and not result.error and result.events is not None
        if fresh_events:
            if result.spend is None:          # the event poll skips `spend`
                result.spend = self.usage.spend
            before = [e["id"] for e in (self.usage.events or [])]
            if [e["id"] for e in result.events] != before:
                log("events: %s" % (", ".join(e["label"] for e in result.events) or "none"))
        elif self.events_enabled():
            result.events = self.usage.events
            result.events_checked = self.usage.events_checked
        self.usage = result
        if not result.error:
            save_usage_cache(result)
        self.note_rate_limit(result)
        self.update_icon()
        self.check_notifications()
        if self.flyout is not None:
            self.flyout.refresh(self.usage)

    def check_notifications(self):
        """Announce each threshold once per limit window - never twice for the
        same level, never again for a level already passed, and not again after
        a restart. Every watched limit is checked on its own, so a weekly limit
        running out is announced while the session is fine; alerts that fall
        due on the same poll share one toast instead of replacing each other."""
        n = self.cfg.get("notifications") or {}
        if not n.get("enabled"):
            return
        before = dict(self.last_notified)
        due = []
        signed_out = self._check_signed_out(n)
        if signed_out:
            due.append(signed_out)
        if not self.usage.error:
            metrics = n.get("metrics") or [n.get("metric", "session")]
            # Reaching a limit is always worth saying, whatever the list omits.
            thresholds = sorted(set(float(t) for t in (n.get("at") or [])) | {100.0})
            for identity, lim in watched_limits(self.usage, metrics):
                alert = self._notify_limit(identity, lim, thresholds)
                if alert:
                    due.append(alert)
        if n.get("events", True):
            due.extend(self._grant_alerts())
        if due:
            if len(due) == 1:
                title, body = due[0][2], due[0][3]
            else:
                title = "Claude usage"
                body = "\n".join(alert[4] for alert in due)
            # Only count it as announced if it was actually shown: a toast held
            # back because you are in a game or a video is tried again next
            # poll rather than silently dropped.
            if self.notify(title, body) is not False:
                for alert in due:
                    self.last_notified[alert[0]] = alert[1]
        if self.last_notified != before:
            save_notify_state(self.state_path, self.last_notified)

    def _check_signed_out(self, n):
        """Numbers that silently stop updating are worse than a nudge. Once
        sign-in has been failing for a quarter of an hour, say so - once per
        outage. Only a successful poll ends an outage: going offline in the
        middle of one does not start the count again."""
        error = str(self.usage.error or "")
        if not error:
            self._signed_out_since = None
            self.last_notified.pop("__signed_out__", None)
            return None
        missing = error == "Not signed in to Claude Code"
        if not (missing or error.startswith("Signed out")):
            return None
        if self._signed_out_since is None:
            self._signed_out_since = time.time()
        if "__signed_out__" in self.last_notified:
            return None
        if time.time() - self._signed_out_since < float(n.get("signed_out_after", 900)):
            return None
        title = "Claude usage is not updating"
        body = ("Claude Code is not signed in on this PC. Run claude and log in."
                if missing else
                "Claude Code's sign-in has expired. Run any claude command to refresh it.")
        return "__signed_out__", ("", 1), title, body, title

    def _grant_alerts(self):
        """One alert per grant, ever, on the first poll that can show it - held
        back while you are busy, it is simply due again next poll. A grant that
        cannot be used yet waits until it can."""
        out = []
        for event in live_events(self.usage):
            key = "grant:" + event["id"]
            if key in self.last_notified or event.get("usable_now") is False:
                continue
            ends = parse_reset(event.get("ends_at"))
            what = describe_clears(event.get("clears") or [])
            left = event.get("resets_left")
            title = ("Free limit reset available" if left == 1
                     else "%s free limit resets available" % left if left else event["label"])
            body = event["label"]
            if what:
                body += " - puts your %s back to full" % what
            if ends is not None:
                body += ". Use it before %s in Settings > Usage." % ends.strftime("%a %d %b %H:%M")
            line = title if title == event["label"] else "%s: %s" % (title, event["label"])
            out.append((key, (event.get("ends_at") or "", 1), title, body, line))
        return out

    def _notify_limit(self, key, lim, thresholds):
        """Returns (key, record, title, body, line) when an alert is due."""
        pct = float(lim["percent"])
        window = window_key(lim.get("resets_at"))
        crossed = [t for t in thresholds if pct >= t]
        level = max(crossed) if crossed else None

        prev_window, prev_level = self.last_notified.get(key, (None, None))
        if window != prev_window:       # a genuinely new window: start fresh
            prev_level = None
        elif prev_level is not None and pct < min(thresholds) / 2.0:
            # Inside one window usage only falls that far when the limit was
            # reset early - a redeemed limit reset. Running out again is news.
            prev_level = None
        if level is None or (prev_level is not None and level <= prev_level):
            self.last_notified[key] = (window, prev_level)
            return None

        reset = parse_reset(lim.get("resets_at"))
        when = ""
        if reset is not None:
            local = reset.astimezone()
            fmt = "%H:%M" if (reset - datetime.now(timezone.utc)).total_seconds() < 86400 else "%a %H:%M"
            when = "resets %s (in %s)" % (local.strftime(fmt), human_delta(reset))
        if level >= 100:
            title = "%s limit reached" % lim["label"]
            # not .capitalize(): it lowercases the rest, turning "Sat" into "sat"
            body = (when[:1].upper() + when[1:]) if when else "No reset time reported."
        else:
            title = "Claude usage %d%%" % round(pct)
            body = "%s at %d%%%s" % (lim["label"], round(pct), (" - " + when) if when else "")
        # and a one-line form, for when several alerts share a toast
        line = ("%s - %s" % (title, when) if level >= 100 and when
                else title if level >= 100 else body)
        return key, (window, level), title, body, line

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

    def panel_corner(self):
        """Which side the flyout and toast open on: next to the overlay when
        there is one, bottom-right like a system flyout otherwise."""
        if self.widget is not None:
            return str((self.cfg.get("taskbar_widget") or {}).get("corner", "left"))
        return "right"

    def sync_widget(self):
        """Create or drop the taskbar overlay to match the config."""
        want = bool((self.cfg.get("taskbar_widget") or {}).get("enabled"))
        if want and self.widget is None:
            self.widget = TaskbarWidget(self)
            log("taskbar overlay on")
        elif not want and self.widget is not None:
            self.widget.destroy()
            self.widget = None
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
        if self.toast is not None:
            self.toast.destroy()
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


# Fluent surface colours, straight from the Windows 11 palette, so the flyout
# reads as a system surface rather than as someone's themed app window.
FLUENT = {
    "light": {
        "surface": "#F9F9F9", "border": "#E5E5E5", "text": "#1A1A1A",
        "muted": "#5D5D5D", "track": "#D6D6D6", "divider": "#EAEAEA",
        "button": "#FDFDFD", "button_border": "#D9D9D9", "button_hover": "#F2F2F2",
        "accent_text": "#FFFFFF",
        "good": "#0F7B0F", "caution": "#9D5D00", "critical": "#C42B1C",
    },
    "dark": {
        "surface": "#2C2C2C", "border": "#1D1D1D", "text": "#FFFFFF",
        "muted": "#C7C7C7", "track": "#4A4A4A", "divider": "#3A3A3A",
        "button": "#383838", "button_border": "#454545", "button_hover": "#414141",
        "accent_text": "#000000",
        "good": "#6CCB5F", "caution": "#FCE100", "critical": "#FF99A4",
    },
}

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_ROUND = 2

try:
    dwmapi = ctypes.WinDLL("dwmapi")
except Exception:
    dwmapi = None


def round_window_corners(hwnd):
    """Windows 11 rounds its own flyouts; a borderless Tk window stays square
    unless we ask DWM for the same treatment."""
    if dwmapi is None or not hwnd:
        return
    try:
        value = ctypes.c_int(DWMWA_ROUND)
        dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                     ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def ui_scale():
    """Pixels per logical pixel on the display the taskbar lives on - which is
    not the system DPI once a second monitor scales differently."""
    try:
        info = taskbar_info()
        if info is not None:
            rect = info[0]
            centre = wt.POINT(int((rect.left + rect.right) / 2),
                              int((rect.top + rect.bottom) / 2))
            monitor = user32.MonitorFromPoint(centre, MONITOR_DEFAULTTONEAREST)
            x, y = wt.UINT(), wt.UINT()
            if monitor and ctypes.windll.shcore.GetDpiForMonitor(
                    monitor, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                return max(1.0, x.value / 96.0)
    except Exception:
        pass
    try:
        return max(1.0, (user32.GetDpiForSystem() or 96) / 96.0)
    except Exception:
        return 1.0


class Flyout(object):
    """The details panel. Modelled on the Windows 11 quick-settings flyouts:
    system surface colour, rounded corners, accent-coloured primary button,
    Segoe UI Variable type, and it closes when you click away."""

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.scale = ui_scale()
        self._images = []          # keep PhotoImages alive
        self._hwnd = None

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.outer = tk.Frame(self.win)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = tk.Frame(self.outer)
        self.body.pack(fill="both", expand=True)
        self.win.bind("<Escape>", lambda e: self.hide())
        if (app.cfg["flyout"] or {}).get("close_on_focus_loss", True):
            self.win.bind("<FocusOut>", lambda e: self.hide())

    # -- theme -------------------------------------------------------------
    def theme(self):
        want = str((self.app.cfg["flyout"] or {}).get("theme", "auto")).lower()
        if want not in ("light", "dark"):
            want = "light" if windows_uses_light_theme() else "dark"
        return FLUENT[want]

    def accent(self):
        value = (self.app.cfg["flyout"] or {}).get("accent", "auto")
        return windows_accent_color() if value == "auto" else value

    def px(self, logical):
        return max(1, int(round(logical * self.scale)))

    def font(self, size, weight="normal"):
        family = self._family()
        return (family, -self.px(size), "bold" if weight == "bold" else "normal")

    def _family(self):
        if getattr(self, "_cached_family", None):
            return self._cached_family
        try:
            import tkinter.font as tkfont
            available = set(tkfont.families(root))
        except Exception:
            available = set()
        for name in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
            if name in available:
                self._cached_family = name
                return name
        self._cached_family = "Segoe UI"
        return self._cached_family

    def severity_color(self, pct):
        """The same vivid colour the widget uses, straight from `thresholds`."""
        return color_for(pct, self.app.cfg["thresholds"])

    def text_color_for(self, pct, light):
        """The bar can be vivid; the percentage beside it is small text, so on a
        light surface it needs deepening to stay readable (amber especially)."""
        color = self.severity_color(pct)
        return shade(color, -0.28) if light else shade(color, 0.08)

    # -- drawn pieces ------------------------------------------------------
    def _bar(self, width, pct, color, colors):
        """A filled bar with a gradient along it - flat colour at this size reads
        as a grey-ish slab, the gradient is what makes it look lit."""
        h = self.px(8)
        ss = 4
        W, H = width * ss, h * ss
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        radius = H / 2.0
        ImageDraw.Draw(img).rounded_rectangle((0, 0, W - 1, H - 1), radius=radius,
                                              fill=hex_to_rgba(colors["track"]))
        span = int((W - 1) * max(0.0, min(100.0, pct)) / 100.0)
        if span > 0:
            span = max(span, int(H))
            start, end = hex_to_rgba(shade(color, 0.28)), hex_to_rgba(color)
            fill = Image.new("RGBA", (span, H))
            paint = ImageDraw.Draw(fill)
            for x in range(span):
                t = x / float(max(1, span - 1))
                paint.line([(x, 0), (x, H)],
                           fill=tuple(int(start[i] + (end[i] - start[i]) * t) for i in range(4)))
            mask = Image.new("L", (span, H), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, span - 1, H - 1), radius=radius, fill=255)
            img.paste(fill, (0, 0), mask)
        photo = ImageTk.PhotoImage(img.resize((width, h), Image.LANCZOS))
        self._images.append(photo)
        return photo

    def _event_card(self, event, colors, content):
        """A live promotion: what it is, what it does, when it runs out, and
        where to use it. Claiming it is left to you - it is a one-off."""
        gold = self.app.cfg.get("taskbar_widget", {}).get("event_color", "#F59E0B")
        card = tk.Frame(self.body, bg=colors["surface"])
        card.pack(fill="x", pady=(self.px(12), 0))
        head = tk.Frame(card, bg=colors["surface"])
        head.pack(fill="x")
        tk.Label(head, text="✦", bg=colors["surface"], fg=gold,
                 font=self.font(14, "bold")).pack(side="left", anchor="n")
        tk.Label(head, text=event["label"], bg=colors["surface"], fg=colors["text"],
                 font=self.font(13, "bold"), anchor="w", justify="left",
                 wraplength=content - self.px(22)).pack(side="left", fill="x", padx=(self.px(4), 0))

        details = []
        left, total = event.get("resets_left"), event.get("resets_total")
        if left is not None:
            details.append("%s of %s reset%s left" % (left, total or left, "" if (total or left) == 1 else "s"))
        what = describe_clears(event.get("clears") or [])
        if what:
            details.append("Puts your %s back to full" % what)
        ends = parse_reset(event.get("ends_at"))
        if ends is not None:
            details.append("Expires %s · in %s" % (ends.strftime("%a %d %b %H:%M"), human_delta(ends)))
        if event.get("usable_now") is False:
            details.append("Not usable yet")
        for line in details:
            tk.Label(card, text=line, bg=colors["surface"], fg=colors["muted"], font=self.font(12),
                     anchor="w", justify="left").pack(fill="x", padx=(self.px(22), 0))
        self._button(card, "Use it in Settings", lambda: webbrowser.open(USAGE_PAGE),
                     colors).pack(anchor="w", padx=(self.px(22), 0), pady=(self.px(6), 0))

    _SPLIT = ("#8B5CF6", "#06B6D4", "#F97316", "#EC4899", "#84CC16",
              "#3B82F6", "#EAB308", "#14B8A6")

    def _split_color(self, index, colors):
        return self._SPLIT[index % len(self._SPLIT)]

    def _split_bar(self, width, shares, colors):
        """One rounded bar divided by each product's share of the week."""
        h = self.px(8)
        ss = 4
        W, H = width * ss, h * ss
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H / 2.0, fill=hex_to_rgba(colors["track"]))
        total = sum(pct for _, pct in shares) or 1.0
        x = 0.0
        paint = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(paint)
        for i, (_, pct) in enumerate(shares):
            span = (W - 1) * pct / max(total, 100.0)
            pd.rectangle((x, 0, x + span, H - 1), fill=hex_to_rgba(self._split_color(i, colors)))
            x += span
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=H / 2.0, fill=255)
        img.paste(paint, (0, 0), Image.composite(mask, Image.new("L", (W, H), 0), paint.split()[3]))
        photo = ImageTk.PhotoImage(img.resize((width, h), Image.LANCZOS))
        self._images.append(photo)
        return photo

    def _button_images(self, text, colors, accent=False):
        """Rounded Fluent buttons, rendered twice for the hover state."""
        pad_x, height, radius = self.px(12), self.px(30), self.px(4)
        font_px = self.px(13)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        pil_font = load_font({"font_file": "segoeui.ttf"}, font_px)
        width = int(probe.textlength(text, font=pil_font)) + pad_x * 2

        out = []
        for hovered in (False, True):
            ss = 3
            img = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            if accent:
                base = self.accent()
                fill = hex_to_rgba(base, 235 if hovered else 255)
                border = fill
                fg = colors["accent_text"]
            else:
                fill = hex_to_rgba(colors["button_hover"] if hovered else colors["button"])
                border = hex_to_rgba(colors["button_border"])
                fg = colors["text"]
            d.rounded_rectangle((0, 0, width * ss - 1, height * ss - 1), radius=radius * ss,
                                fill=fill, outline=border, width=ss)
            big = load_font({"font_file": "segoeui.ttf"}, font_px * ss)
            draw_text_centered(d, (0, 0, width * ss, height * ss), text, big,
                               hex_to_rgba(fg), False)
            photo = ImageTk.PhotoImage(img.resize((width, height), Image.LANCZOS))
            self._images.append(photo)
            out.append(photo)
        return out

    def _button(self, parent, text, command, colors, accent=False):
        normal, hover = self._button_images(text, colors, accent)
        label = tk.Label(parent, image=normal, bd=0, highlightthickness=0,
                         bg=colors["surface"], cursor="hand2")
        label.bind("<Enter>", lambda e: label.configure(image=hover))
        label.bind("<Leave>", lambda e: label.configure(image=normal))
        label.bind("<Button-1>", lambda e: command())
        return label

    # -- lifecycle ---------------------------------------------------------
    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self._images = []

    def refresh(self, usage):
        """Only rebuild when something actually changed: a full rebuild drops
        button hover states and flashes the panel."""
        if not self.visible:
            return
        if self.content_key(usage) == getattr(self, "_content_key", None):
            return
        self.render(usage)
        self.win.update_idletasks()
        self.win.geometry("%dx%d" % (self.win.winfo_reqwidth(), self.win.winfo_reqheight()))

    @staticmethod
    def content_key(usage):
        """Everything the panel shows, so refresh() rebuilds only on a change."""
        return (usage.error, usage.updated,
                tuple((l["label"], round(l["percent"], 2), l["resets_at"]) for l in usage.limits),
                tuple((b["key"], b["percent"]) for b in (usage.buckets or [])),
                tuple((r.get("key"), r.get("percent")) for r in (usage.breakdown or [])),
                tuple((e["id"], e.get("resets_left"), e.get("usable_now"))
                      for e in live_events(usage)))

    def render(self, usage):
        self._content_key = self.content_key(usage)
        colors = self.theme()
        cfg = self.app.cfg["flyout"] or {}
        self._clear()
        pad = self.px(16)
        width = self.px(int(cfg.get("width", 340)))
        content = width - 2 * pad

        self.win.configure(bg=colors["border"])
        self.outer.configure(bg=colors["surface"])
        self.body.configure(bg=colors["surface"], padx=pad, pady=pad)

        def label(parent, text, size=13, weight="normal", color=None, **pack):
            widget = tk.Label(parent, text=text, bg=colors["surface"],
                              fg=color or colors["text"], font=self.font(size, weight),
                              anchor="w", justify="left")
            widget.pack(**pack)
            return widget

        header = tk.Frame(self.body, bg=colors["surface"])
        header.pack(fill="x")
        label(header, "Claude usage", size=16, weight="bold", side="left")
        stamp = "Updated %s" % usage.updated.strftime("%H:%M") if usage.updated else "No data yet"
        label(header, stamp, size=12, color=colors["muted"], side="right")

        if usage.error:
            note = tk.Frame(self.body, bg=colors["surface"])
            note.pack(fill="x", pady=(self.px(10), 0))
            text = usage.error
            if self.app.retry_at and time.time() < self.app.retry_at:
                text += " · retrying in %s" % human_delta(
                    datetime.fromtimestamp(self.app.retry_at, timezone.utc))
            label(note, text, size=12, color=colors["caution"], anchor="w", fill="x")

        light = colors is FLUENT["light"]
        for event in live_events(usage):
            self._event_card(event, colors, content)

        for lim in usage.limits:
            pct = float(lim["percent"])
            color = self.severity_color(pct)
            row = tk.Frame(self.body, bg=colors["surface"])
            row.pack(fill="x", pady=(self.px(14), 0))
            label(row, lim["label"], size=13, side="left")
            label(row, "%d%%" % round(pct), size=13, weight="bold",
                  color=self.text_color_for(pct, light), side="right")

            bar = tk.Label(self.body, image=self._bar(content, pct, color, colors),
                           bd=0, highlightthickness=0, bg=colors["surface"])
            bar.pack(fill="x", pady=(self.px(6), 0))

            reset = parse_reset(lim["resets_at"])
            if reset:
                label(self.body,
                      "Resets %s · in %s" % (reset.strftime("%a %H:%M"), human_delta(reset)),
                      size=12, color=colors["muted"], anchor="w", fill="x",
                      pady=(self.px(4), 0))

        # Where this week's usage went, as one stacked bar and a legend.
        shares = [(r.get("display_name") or r.get("key") or "?", float(r.get("percent") or 0))
                  for r in (usage.breakdown or [])]
        shares = [(name, pct) for name, pct in shares if pct > 0]
        if shares:
            label(self.body, "This week by product", size=12, color=colors["muted"],
                  anchor="w", fill="x", pady=(self.px(16), 0))
            split = tk.Label(self.body, image=self._split_bar(content, shares, colors),
                             bd=0, highlightthickness=0, bg=colors["surface"])
            split.pack(fill="x", pady=(self.px(6), 0))
            # Laid out in rows by measured width, so a long list wraps instead
            # of widening the panel. No border or padding on these labels, so
            # the font's measurement is their whole width.
            import tkinter.font as tkfont
            text_font, dot_font = tkfont.Font(font=self.font(12)), tkfont.Font(font=self.font(11))
            gap = self.px(4) + self.px(12)
            legend, used = None, 0
            for i, (name, pct) in enumerate(shares):
                text = "%s %d%%" % (name, round(pct))
                need = dot_font.measure("\u25cf") + text_font.measure(text) + gap
                if legend is None or used + need > content:
                    legend = tk.Frame(self.body, bg=colors["surface"])
                    legend.pack(fill="x", pady=(self.px(4 if used == 0 else 2), 0))
                    used = 0
                used += need
                for text_, font_, fg_, padx_ in (
                        ("\u25cf", self.font(11), self._split_color(i, colors), 0),
                        (text, self.font(12), colors["muted"], (self.px(4), self.px(12)))):
                    tk.Label(legend, text=text_, bg=colors["surface"], fg=fg_, font=font_,
                             bd=0, padx=0, highlightthickness=0).pack(side="left", padx=padx_)

        # Any other usage pool the endpoint reports - promotions and new
        # products appear here before anyone gives them a proper name. An idle
        # pool (0%, no window, not locked) is just a name the API happens to
        # send, so it stays out of the way.
        for pool in (usage.buckets or []):
            if not (pool["percent"] > 0 or pool.get("resets_at") or pool.get("locked_reason")
                    or pool.get("promotional")):
                continue
            row = tk.Frame(self.body, bg=colors["surface"])
            row.pack(fill="x", pady=(self.px(12), 0))
            label(row, pool["label"], size=13, side="left")
            if pool.get("promotional"):
                label(row, "  promotion", size=11, weight="bold",
                      color=self.accent(), side="left")
            label(row, "%d%%" % round(pool["percent"]), size=13, weight="bold",
                  color=self.text_color_for(pool["percent"], light), side="right")
            bits = []
            reset = parse_reset(pool.get("resets_at"))
            if reset:
                bits.append("Resets %s · in %s" % (reset.strftime("%a %H:%M"), human_delta(reset)))
            if pool.get("locked_reason"):
                bits.append("Locked: %s" % pool["locked_reason"])
            if bits:
                label(self.body, "   ".join(bits), size=12, color=colors["muted"],
                      anchor="w", fill="x", pady=(self.px(4), 0))

        extra = usage.extra or {}
        if extra.get("is_enabled"):
            label(self.body, "Extra usage: %d%% of the monthly limit"
                  % round(float(extra.get("utilization") or 0)),
                  size=12, color=colors["muted"], anchor="w", fill="x",
                  pady=(self.px(12), 0))

        divider = tk.Frame(self.body, bg=colors["divider"], height=1)
        divider.pack(fill="x", pady=(self.px(16), 0))

        footer = tk.Frame(self.body, bg=colors["surface"])
        footer.pack(fill="x", pady=(self.px(12), 0))
        self._button(footer, "Refresh", lambda: self.app.start_fetch(manual=True),
                     colors, accent=True).pack(side="left")
        self._button(footer, "Usage page",
                     lambda: webbrowser.open(self.app.cfg.get("usage_page_url")),
                     colors).pack(side="left", padx=(self.px(8), 0))
        self._button(footer, "Settings", lambda: os.startfile(CONFIG_PATH),
                     colors).pack(side="left", padx=(self.px(8), 0))

    def show(self, usage):
        cfg = self.app.cfg["flyout"] or {}
        self.scale = ui_scale()
        self.render(usage)
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        h = self.win.winfo_reqheight()

        rect = wt.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        ox, oy = cfg.get("corner_offset", [12, 12])
        ox, oy = self.px(int(ox)), self.px(int(oy))
        if self.app.panel_corner() == "left":
            x = rect.left + ox
        else:
            x = rect.right - w - ox
        # With the taskbar on top, the work area's bottom is the far side of the
        # screen from the widget; open next to the widget instead.
        info = taskbar_info()
        on_top = info is not None and info[1] == ABE_TOP
        y = rect.top + oy if on_top else rect.bottom - h - oy
        self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))

        self.win.attributes("-alpha", 0.0)
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.visible = True
        if self._hwnd is None:
            self._hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        round_window_corners(self._hwnd)
        self._fade(0.0)

    def _fade(self, value):
        """Windows fades its flyouts in; a window that just appears feels wrong."""
        if not self.visible:
            return
        value = min(1.0, value + 0.2)
        try:
            self.win.attributes("-alpha", value)
        except Exception:
            return
        if value < 1.0:
            self.win.after(16, lambda: self._fade(value))

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
GWL_STYLE = -16
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
HWND_TOP = 0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
GW_HWNDPREV = 3
MONITOR_DEFAULTTONEAREST = 2
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
ERROR_CLASS_ALREADY_EXISTS = 1410

WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
WM_DPICHANGED = 0x02E0
WM_THEMECHANGED = 0x031A

# Windows' own "is the user busy" signal: a game, a fullscreen video, a
# presentation. Used to hold back toasts, the same way Windows holds back its own.
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.UpdateLayeredWindow.restype = wt.BOOL
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT),
                                       ctypes.POINTER(SIZE), wt.HDC, ctypes.POINTER(wt.POINT),
                                       wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.IsWindow.argtypes = [wt.HWND]
user32.SetParent.restype = wt.HWND
user32.SetParent.argtypes = [wt.HWND, wt.HWND]
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.MonitorFromPoint.restype = wt.HANDLE
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
if hasattr(user32, "GetWindowLongPtrW"):
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
shell32.SHAppBarMessage.restype = ctypes.c_size_t
shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(APPBARDATA)]
try:
    shell32.SHQueryUserNotificationState.restype = ctypes.c_long
    shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
except Exception:
    pass

GA_PARENT = 1


def taskbar_info():
    """(rect, edge, autohidden) for the taskbar, or None if it can't be found."""
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    if not shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(data)):
        return None
    state = shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(data))
    return data.rc, data.uEdge, bool(state & ABS_AUTOHIDE)


def user_is_busy():
    """True when Windows says a game, a fullscreen video or a presentation owns
    the screen. This is the signal Windows itself uses to hold back toasts."""
    try:
        state = ctypes.c_int(0)
        if shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)
    except Exception:
        pass
    return False


def short_error(error):
    """The overlay has room for a word, not a sentence."""
    if not error:
        return None
    lowered = str(error).lower()
    if "rate limited" in lowered:
        return "paused"
    if "offline" in lowered:
        return "offline"
    if "signed" in lowered or "sign in" in lowered:
        return "sign in"
    if "loading" in lowered:
        return "…"
    return "no data"


def windows_accent_color():
    """The user's accent colour, as Windows stores it (ABGR)."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
        with key:
            value = int(winreg.QueryValueEx(key, "AccentColor")[0]) & 0xFFFFFF
        return "#%02X%02X%02X" % (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)
    except Exception:
        return "#0F6CBD"


def windows_uses_light_theme():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except Exception:
        return False


def theme_ink(light=None):
    """Ink for glyphs that have to read against the taskbar itself rather than
    against a fill of ours. Near-black on light, near-white on dark - the flat
    extremes look like a hole punched in the bar, so both are pulled back a
    step, the same pair the widget uses for its text."""
    if light is None:
        light = windows_uses_light_theme()
    return "#1A1A1A" if light else "#F2F2F2"


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


class TaskbarWidget(object):
    """The always-visible readout, drawn as part of the taskbar.

    A layered window that is a real CHILD of Shell_TrayWnd (created as a popup
    and reparented with SetParent - creating it as a child outright is refused).
    That gives the one property that matters: it is exactly as visible as the
    taskbar itself, no more and no less. It is covered by whatever covers the
    taskbar (a fullscreen video, Task View), it stays put when the taskbar is
    clicked or the Start menu opens, and it slides away with an auto-hiding
    taskbar - all done by Windows, with no detection code here.

    It paints with UpdateLayeredWindow (per-pixel alpha), so the taskbar's own
    acrylic shows through around the glyphs, and every frame lands as one
    composited update.
    """

    CLASS_NAME = "ClaudeUsageOverlayWnd"
    _atom = None
    _proc = None                      # one WNDPROC for the class, kept alive here
    _windows = {}                     # hwnd -> instance, so the proc can dispatch

    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self.geometry = None          # (x, y, w, h) in the taskbar's client space
        self._last_key = None
        self._dc = None
        self._bitmap = None
        self._old_bitmap = None
        self._bits = None
        self._dc_size = None
        self._image = None
        self._last_blit_error = None
        self._retry_at = 0.0
        self._retry_backoff = 2.0
        self._create()

    # -- window ------------------------------------------------------------
    @classmethod
    def _dispatch(cls, hwnd, msg, wparam, lparam):
        """The window class owns one procedure; it routes to whichever instance
        owns the window. An exception here would be swallowed by ctypes and
        leave the window half-alive, so nothing is allowed to escape."""
        try:
            self = cls._windows.get(int(hwnd or 0))
            if self is not None:
                handled = self._on_message(msg, wparam, lparam)
                if handled is not None:
                    return handled
        except Exception:
            log("overlay wndproc: %s" % traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create(self):
        """Build the window, or return False and let tick() try again later."""
        if TaskbarWidget._atom is None:
            if TaskbarWidget._proc is None:
                TaskbarWidget._proc = WNDPROC(TaskbarWidget._dispatch)
            wc = WNDCLASS()
            wc.lpfnWndProc = TaskbarWidget._proc
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = self.CLASS_NAME
            ctypes.set_last_error(0)
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom and ctypes.get_last_error() != ERROR_CLASS_ALREADY_EXISTS:
                return False
            TaskbarWidget._atom = atom or True

        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        if not taskbar:
            return False                  # the shell is between lives; try later

        hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.CLASS_NAME, "Claude usage", WS_POPUP, 0, 0, 10, 10,
            None, None, kernel32.GetModuleHandleW(None), None)
        if not hwnd:
            log("overlay window creation failed: %s" % ctypes.get_last_error())
            return False

        # Popup -> child, then hand it to the taskbar. A refused SetParent
        # leaves a stray popup behind, so destroy it rather than keep it.
        get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        put(hwnd, GWL_STYLE, (get(hwnd, GWL_STYLE) & ~WS_POPUP) | WS_CHILD)
        ctypes.set_last_error(0)
        if not user32.SetParent(hwnd, taskbar):
            log("overlay could not attach to the taskbar: %s" % ctypes.get_last_error())
            user32.DestroyWindow(hwnd)
            return False

        self.hwnd = hwnd
        TaskbarWidget._windows[int(hwnd)] = self
        self.geometry = None
        self._last_key = None
        return True

    def _on_message(self, msg, wparam, lparam):
        """Return None for anything we don't handle."""
        if msg == WM_LBUTTONUP:
            self._on_left()
            return 0
        if msg == WM_RBUTTONUP:
            self.app.show_menu()
            return 0
        if msg in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE, WM_DPICHANGED, WM_THEMECHANGED):
            self.geometry = None      # re-measure against the new taskbar/theme
            self._last_key = None
            return 0
        return None

    def _on_left(self):
        action = self.app.cfg.get("left_click", "flyout")
        if action == "flyout":
            self.app.toggle_flyout()
        elif action == "refresh":
            self.app.start_fetch(manual=True)
        elif action == "web":
            webbrowser.open(self.app.cfg.get("usage_page_url"))

    def cfg(self):
        return self.app.cfg.get("taskbar_widget") or {}

    def reconfigure(self):
        self._last_key = None
        self.geometry = None

    def screen_rect(self):
        """Where the widget is on screen, for anchoring the flyout and toast."""
        if not self.hwnd:
            return None
        r = wt.RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(r)):
            return None
        return r

    def destroy(self):
        self._release_dc()
        if self.hwnd:
            TaskbarWidget._windows.pop(int(self.hwnd), None)
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None

    # -- surface -----------------------------------------------------------
    def _release_dc(self):
        if self._dc:
            if self._old_bitmap:
                gdi32.SelectObject(self._dc, self._old_bitmap)
            gdi32.DeleteDC(self._dc)
        if self._bitmap:
            gdi32.DeleteObject(self._bitmap)
        self._dc = self._bitmap = self._old_bitmap = self._bits = self._dc_size = None

    def _ensure_dc(self, w, h):
        if self._dc_size == (w, h) and self._dc:
            return True
        self._release_dc()
        screen = user32.GetDC(None)
        if not screen:
            return False
        try:
            self._dc = gdi32.CreateCompatibleDC(screen)
            if not self._dc:
                return False
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = w
            info.bmiHeader.biHeight = -h          # top-down
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = BI_RGB
            bits = ctypes.c_void_p()
            self._bitmap = gdi32.CreateDIBSection(self._dc, ctypes.byref(info), DIB_RGB_COLORS,
                                                  ctypes.byref(bits), None, 0)
            if not self._bitmap:
                return False
            self._bits = bits
            self._old_bitmap = gdi32.SelectObject(self._dc, self._bitmap)
            self._dc_size = (w, h)
            return True
        finally:
            user32.ReleaseDC(None, screen)

    def _blit(self, image):
        """Push a fresh frame to DWM in one atomic call. Position and size are
        the window's own (set with SetWindowPos in the taskbar's client space);
        only the pixels travel here."""
        if not self.hwnd or self.geometry is None:
            return False
        _, _, w, h = self.geometry
        if not self._ensure_dc(w, h):
            return False
        data = premultiply(image).tobytes("raw", "BGRA")
        ctypes.memmove(self._bits, data, len(data))
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        src, size = wt.POINT(0, 0), SIZE(w, h)
        screen = user32.GetDC(None)
        if not screen:
            return False
        try:
            ctypes.set_last_error(0)
            ok = user32.UpdateLayeredWindow(self.hwnd, screen, None, ctypes.byref(size),
                                            self._dc, ctypes.byref(src), 0,
                                            ctypes.byref(blend), ULW_ALPHA)
            if not ok:
                # Silently ignoring this is how a blank widget goes unnoticed.
                err = ctypes.get_last_error()
                if err != self._last_blit_error:
                    self._last_blit_error = err
                    log("UpdateLayeredWindow failed: err=%s" % err)
                return False
            self._last_blit_error = None
            return True
        finally:
            user32.ReleaseDC(None, screen)

    # -- placement ---------------------------------------------------------
    def _target_geometry(self, taskbar):
        """Where to sit inside the taskbar, or None for a vertical taskbar."""
        info = taskbar_info()
        if info is None or info[1] not in (ABE_TOP, ABE_BOTTOM):
            return None
        client = wt.RECT()
        if not user32.GetClientRect(taskbar, ctypes.byref(client)):
            return None
        width, height = client.right - client.left, client.bottom - client.top
        if width <= 0 or height <= 0:
            return None

        c = self.cfg()
        scale = height / 48.0                      # 48px is the 100% DPI taskbar
        pad = max(2, int(float(c.get("padding", 5)) * scale))
        h = max(12, height - 2 * pad)
        w = max(40, int(float(c.get("width", 128)) * scale))
        ox, oy = (c.get("offset") or [8, 0])[:2]
        ox, oy = int(float(ox) * scale), int(float(oy) * scale)
        x = width - w - ox if str(c.get("corner", "left")) == "right" else ox
        y = (height - h) // 2 + oy
        return x, y, w, h

    def tick(self):
        """Called twice a second: liveness, placement, content."""
        if self.hwnd and not user32.IsWindow(self.hwnd):
            # Explorer restarted: the taskbar took its children with it.
            log("overlay window went away with the taskbar; rebuilding")
            TaskbarWidget._windows.pop(int(self.hwnd), None)
            self.hwnd = None
            self._release_dc()
        if not self.hwnd:
            now = time.time()
            if now < self._retry_at:
                return
            if not self._create():
                self._retry_backoff = min(60.0, self._retry_backoff * 2)
                self._retry_at = now + self._retry_backoff
                return
            self._retry_backoff = 2.0

        taskbar = user32.GetAncestor(self.hwnd, GA_PARENT)
        geometry = self._target_geometry(taskbar)
        if geometry is None:
            if self.geometry is not None:
                user32.ShowWindow(self.hwnd, SW_HIDE)
                self.geometry = None
            return
        if geometry != self.geometry:
            x, y, w, h = geometry
            self.geometry = geometry
            self._last_key = None
            user32.SetWindowPos(self.hwnd, wt.HWND(HWND_TOP), x, y, w, h, SWP_NOACTIVATE)
        # The taskbar's own content is a sibling; if it ever ends up in front
        # of us, step back in front of it. Read-only unless something changed.
        if user32.GetWindow(self.hwnd, GW_HWNDPREV):
            user32.SetWindowPos(self.hwnd, wt.HWND(HWND_TOP), 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self.refresh(self.app.usage)
        if not user32.IsWindowVisible(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)

    # -- drawing -----------------------------------------------------------
    def _colors(self):
        """No background is painted, so these only have to stay legible on the
        real taskbar - which means following the Windows theme."""
        c = self.cfg()
        light = windows_uses_light_theme()
        fg = c.get("text_color", "auto")
        muted = c.get("muted_color", "auto")
        if fg == "auto":
            fg = "#1A1A1A" if light else "#F2F2F2"
        if muted == "auto":
            muted = "#5F6368" if light else "#B9BEC4"
        track = (0, 0, 0, 60) if light else (255, 255, 255, 70)
        return fg, muted, track

    def refresh(self, usage):
        if self.geometry is None:
            return
        c = self.cfg()
        key = c.get("metric") or self.app.cfg["primary_metric"]
        limit = usage.by_key(key)
        pct = float(limit["percent"]) if limit else 0.0
        reset = parse_reset(limit["resets_at"]) if limit else None
        # Keep showing the last known figures; the flyout explains the trouble.
        # A metric the API never returns would otherwise read as a confident 0%.
        error = None if limit else (short_error(usage.error) or "no data")
        # Numbers that stopped being refreshed still deserve to be trusted less:
        # dim them rather than pretend they are live.
        # Dimming is about age, not about whether the last poll failed: the
        # endpoint rate-limits often, and a figure from a minute ago is still
        # worth showing at full strength. The flyout explains the error.
        age = usage.age_seconds()
        stale = age is None or age > max(300.0, 3.0 * float(self.app.cfg["refresh_seconds"]))
        event = bool(live_events(usage)) and bool(c.get("show_events", True))
        state = (round(pct, 1), human_delta(reset), error, stale, event, self.geometry,
                 windows_uses_light_theme())
        if state == self._last_key:
            return
        _, _, w, h = self.geometry
        image = self._render(w, h, pct, reset, error)
        if event:
            draw_sparkle(image, w, h, hex_to_rgba(c.get("event_color", "#F59E0B")))
        if stale:
            image = fade_image(image, float(c.get("stale_opacity", 0.55)))
        self._image = image
        if self._blit(image):
            self._last_key = state

    def _render(self, w, h, pct, reset, error):
        c = self.cfg()
        fg, muted, track = self._colors()
        ss = max(1, int(c.get("supersample", 3)))
        W, H = w * ss, h * ss
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pct = max(0.0, min(100.0, pct))
        color = color_for(pct, self.app.cfg["thresholds"])
        spent = pct >= 99.5

        def font(px, bold=True):
            name = c.get("bold_font_file" if bold else "font_file",
                         "segoeuib.ttf" if bold else "segoeui.ttf")
            return load_font({"font_file": name}, px)

        backdrop = c.get("background", "transparent")
        if backdrop and backdrop != "transparent":
            radius = float(c.get("corner_radius", 6)) * ss
            d.rounded_rectangle((0, 0, W - 1, H - 1), radius=radius,
                                fill=hex_to_rgba(backdrop, int(c.get("background_alpha", 255))))

        if error:
            d.text((0, H / 2), error, font=font(H * 0.44, False),
                   fill=hex_to_rgba(muted), anchor="lm")
            return self._finish(img, w, h)

        # Left: the number (or the skull, same as the tray at 100%).
        num_font = font(H * 0.62)
        if spent:
            skull_w = H * 0.52
            draw_skull(img, (0, H * 0.18, skull_w, H * 0.82), hex_to_rgba(fg), False)
            text_end = skull_w
        else:
            label = "%d%%" % round(pct)
            d.text((0, H / 2), label, font=num_font, fill=hex_to_rgba(color), anchor="lm")
            text_end = d.textlength(label, font=num_font)

        x0 = text_end + H * 0.22
        if x0 >= W:
            return self._finish(img, w, h)

        show_reset = bool(c.get("show_reset", True)) and reset is not None
        show_bar = bool(c.get("show_bar", True))
        bar_h = float(c.get("bar_height", 4)) * ss * (h / 38.0)   # 38px tall at 100% DPI
        bar_h = int(max(2 * ss, min(H * 0.22, bar_h)))

        if show_reset:
            small = font(H * 0.36, False)
            text = human_delta(reset)
            while d.textlength(text, font=small) > (W - x0) and len(text) > 4:
                text = text[:-1]
            d.text((x0, H * 0.30), text, font=small, fill=hex_to_rgba(muted), anchor="lm")
            bar_y = H * 0.62
        else:
            bar_y = H / 2 - bar_h / 2

        if show_bar:
            d.rounded_rectangle((x0, bar_y, W - 1, bar_y + bar_h), radius=bar_h / 2, fill=track)
            span = (W - 1 - x0) * pct / 100.0
            if span > 0:
                d.rounded_rectangle((x0, bar_y, x0 + max(span, bar_h), bar_y + bar_h),
                                    radius=bar_h / 2, fill=hex_to_rgba(color))

        return self._finish(img, w, h)

    @staticmethod
    def _finish(img, w, h):
        """Downsample, then float the whole rectangle one step above fully
        transparent: UpdateLayeredWindow hit-tests on alpha, and a click has to
        land anywhere on the widget, not only on a letter. The veil takes the
        theme's own colour so that even those 0.4% are invisible."""
        small = img.resize((w, h), Image.LANCZOS)
        veil = (255, 255, 255, 1) if windows_uses_light_theme() else (0, 0, 0, 1)
        base = Image.new("RGBA", (w, h), veil)
        base.alpha_composite(small)
        return base


class Toast(object):
    """A notification for when there is no tray icon to hang a balloon on.

    Shell_NotifyIcon balloons need a registered, visible icon; running
    overlay-only used to mean the 80%/95% warnings went nowhere at all. This is
    the same Fluent surface as the flyout, above the corner the overlay sits in,
    and it fades itself away.
    """

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.outer = tk.Frame(self.win)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = tk.Frame(self.outer)
        self.body.pack(fill="both", expand=True)
        self._after = None
        self._hwnd = None
        self.scale = ui_scale()

    def px(self, logical):
        return max(1, int(round(logical * self.scale)))

    def show(self, title, message, seconds=8):
        """Returns False when held back, so the caller can retry later."""
        if user_is_busy():
            return False                # don't paint over a game or a call
        colors = FLUENT["light" if windows_uses_light_theme() else "dark"]
        family = "Segoe UI Variable Text"
        self.scale = ui_scale()
        for child in self.body.winfo_children():
            child.destroy()

        self.win.configure(bg=colors["border"])
        self.outer.configure(bg=colors["surface"])
        self.body.configure(bg=colors["surface"], padx=self.px(16), pady=self.px(14))

        tk.Label(self.body, text=title, bg=colors["surface"], fg=colors["text"],
                 font=(family, -self.px(14), "bold"), anchor="w",
                 justify="left").pack(fill="x")
        tk.Label(self.body, text=message, bg=colors["surface"], fg=colors["muted"],
                 font=(family, -self.px(12)), anchor="w", justify="left",
                 wraplength=self.px(300)).pack(fill="x", pady=(self.px(4), 0))
        for widget in (self.win, self.outer, self.body):
            widget.bind("<Button-1>", lambda e: self._clicked())

        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        rect = wt.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)   # work area
        offset = self.px(12)
        x = rect.left + offset if self.app.panel_corner() == "left" else rect.right - w - offset
        info = taskbar_info()
        y = rect.top + offset if (info is not None and info[1] == ABE_TOP) else rect.bottom - h - offset
        self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))

        self.win.attributes("-alpha", 0.0)
        self.win.deiconify()
        self.win.lift()
        if self._hwnd is None:
            self._hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)(self._hwnd, GWL_EXSTYLE)
            getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)(
                self._hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        round_window_corners(self._hwnd)
        self._fade(0.0, +0.2)

        if self._after is not None:
            try:
                root.after_cancel(self._after)
            except Exception:
                pass
        self._after = root.after(int(seconds * 1000), self.hide)
        return True

    def _clicked(self):
        self.hide()
        self.app.toggle_flyout()

    def _fade(self, value, delta):
        value = max(0.0, min(1.0, value + delta))
        try:
            self.win.attributes("-alpha", value)
        except Exception:
            return
        if 0.0 < value < 1.0:
            root.after(16, lambda: self._fade(value, delta))
        elif value <= 0.0:
            self.win.withdraw()

    def hide(self):
        self._after = None
        if self.win.winfo_viewable():
            self._fade(1.0, -0.2)

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main loop: tkinter drives, Win32 messages are pumped from it
# ---------------------------------------------------------------------------

_instance_mutex = None


def set_dpi_awareness():
    """Per-monitor v2 if this Windows has it: v1 leaves non-client areas scaled
    by the system DPI, which puts the overlay in the wrong place on a second
    monitor. The context is a pointer-sized handle - passing a bare int would
    marshal as 32 bits and fail silently on 64-bit."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # PMv2
            return True
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:         # PMv1
            return True
    except Exception:
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except Exception:
        return False


def single_instance():
    """True if we own the single-instance slot.

    The mutex lives in the per-session namespace (the Global one needs a
    privilege we may not have) and ERROR_ALREADY_EXISTS is only believed when a
    real instance window answers - otherwise a stale handle could keep the app
    from ever starting again.
    """
    global _instance_mutex
    ctypes.set_last_error(0)
    _instance_mutex = kernel32.CreateMutexW(None, True, "Local\\ClaudeUsageBarMutex")
    if not _instance_mutex:
        return True                      # can't tell; better to run than not to
    if ctypes.get_last_error() != 183:   # ERROR_ALREADY_EXISTS: we created it
        return True
    # It already existed, so wait briefly for ownership rather than guessing
    # from a window that the other instance may not have created yet.
    return kernel32.WaitForSingleObject(_instance_mutex, 1500) in (0, 0x80)


def main():
    global root
    log("starting (pid %d, %s)" % (os.getpid(), sys.executable))
    if not single_instance():
        log("another instance is running; exiting")
        return
    if not set_dpi_awareness():
        log("DPI awareness could not be set; coordinates may be scaled")

    root = tk.Tk()
    root.withdraw()
    app = TrayApp()

    msg = wt.MSG()

    def every(name, interval_ms, body, first_ms=None):
        """Run `body` forever. An exception inside a Tk timer callback kills its
        chain silently, and under pythonw the traceback goes nowhere - which
        would leave the app running but with one whole job (clicks, polling,
        config reload) quietly dead. Every chain gets the same guard.
        `interval_ms` may be a callable, re-evaluated each time, so an interval
        that lives in config.json follows a reload."""
        def interval():
            try:
                return max(10, int(interval_ms() if callable(interval_ms) else interval_ms))
            except Exception:
                return 1000

        def run():
            try:
                body()
            except Exception:
                log("%s failed: %s" % (name, traceback.format_exc()))
            root.after(interval(), run)
        root.after(interval() if first_ms is None else first_ms, run)

    def pump():
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        app.apply_pending()

    every("pump", 50, pump, first_ms=50)
    every("poll", lambda: max(10, int(app.cfg["refresh_seconds"])) * 1000,
          app.start_fetch, first_ms=100)
    every("config watch", 2000, lambda: app.reload_config() if app.config_changed_on_disk() else None)
    every("tray icons", 5000, app.ensure_icons)
    every("overlay", 500, lambda: app.widget.tick() if app.widget is not None else None,
          first_ms=300)
    every("clock", 30000, app.update_icon)
    root.mainloop()


if __name__ == "__main__":
    try:
        if import_dependencies():
            main()
    except Exception:
        log("fatal: %s" % traceback.format_exc())
        raise
