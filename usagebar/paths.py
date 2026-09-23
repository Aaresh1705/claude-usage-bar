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
LOCAL = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", ".")
DATA_DIR = APP_DIR if _writable(APP_DIR) else os.path.join(LOCAL, "llm-usage-bar")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = APP_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "llm_usage_bar.log")
CRASH_LOG = os.path.join(DATA_DIR, "llm_usage_bar.crash.log")
FALLBACK_LOG = os.path.join(LOCAL, "llm-usage-bar", "llm_usage_bar.log")
ICON_CACHE = os.path.join(DATA_DIR, ".icons")
USAGE_CACHE = os.path.join(DATA_DIR, ".usage_cache.json")
NOTIFY_STATE = os.path.join(DATA_DIR, ".notify_state.json")
POLL_STATE = os.path.join(DATA_DIR, ".poll_state.json")


def migrate_legacy_files():
    """The app used to be called Claude Usage Bar. Carry an install from then
    over: its per-user folder and its logs get the new names. Only the names
    changed - config.json and the caches were never named after it. Called
    once at startup; returns what it moved, for the log."""
    moved = []
    old_dir = os.path.join(LOCAL, "claude-usage-bar")
    new_dir = os.path.join(LOCAL, "llm-usage-bar")
    pairs = [(os.path.join(DATA_DIR, "claude_usage_bar.log"), LOG_PATH),
             (os.path.join(DATA_DIR, "claude_usage_bar.crash.log"), CRASH_LOG)]
    for old, new in pairs:
        try:
            if os.path.exists(old) and not (os.path.exists(new) and os.path.getsize(new)):
                if os.path.exists(new):
                    os.remove(new)                 # an empty one this run just created
                os.rename(old, new)
                moved.append(os.path.basename(old))
        except OSError:
            pass
    try:
        # (importing this module may already have created an empty new one)
        if os.path.isdir(old_dir) and not (os.path.isdir(new_dir) and os.listdir(new_dir)):
            if os.path.isdir(new_dir):
                os.rmdir(new_dir)
            os.rename(old_dir, new_dir)
            moved.append(old_dir)
    except OSError:
        pass
    return moved

