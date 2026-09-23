"""Where settings, caches and logs live."""

import os
import sys


# Frozen into an .exe, __file__ points inside PyInstaller's temporary unpack
# directory; the folder the user actually put the app in is the executable's.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # this file is usagebar/paths.py; the app folder is the one above
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
CRASH_LOG = os.path.join(DATA_DIR, "claude_usage_bar.crash.log")
FALLBACK_LOG = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", "."),
                            "claude-usage-bar", "claude_usage_bar.log")
ICON_CACHE = os.path.join(DATA_DIR, ".icons")
USAGE_CACHE = os.path.join(DATA_DIR, ".usage_cache.json")
NOTIFY_STATE = os.path.join(DATA_DIR, ".notify_state.json")
POLL_STATE = os.path.join(DATA_DIR, ".poll_state.json")
