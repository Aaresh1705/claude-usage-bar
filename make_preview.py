"""Renders preview.png: the widget in every state it has, on both themes.

Unlike make_icon.py this one drives the real renderer - it loads the app as a
module and calls TaskbarWidget._render, so the picture in the README cannot
drift away from what the taskbar actually shows.
"""

import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "preview.png")

W, H = 836, 150          # two 66px bands, with a black margin above and below
BAND = 66
MARGIN = 9
PANEL, PANEL_H = 141, 44
INSET = 20
LIGHT, DARK = (236, 236, 238, 255), (32, 32, 32, 255)

# percent, minutes until the reset, error line
STATES = [(14, 92, None), (72, 41, None), (96, 17, None), (100, 12, None),
          (0, None, "paused")]


def load_app():
    """The app is a .pyw, so it cannot simply be imported by name."""
    spec = importlib.util.spec_from_file_location(
        "claude_usage_bar", os.path.join(HERE, "claude_usage_bar.pyw"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not module.import_dependencies():
        raise SystemExit("Pillow / requests are missing")
    return module


def widget(app, cfg, light):
    """A TaskbarWidget with just enough around it to draw with."""
    app.windows_uses_light_theme = lambda: light
    obj = app.TaskbarWidget.__new__(app.TaskbarWidget)
    obj.app = types.SimpleNamespace(cfg=cfg)
    return obj


def main():
    app = load_app()
    cfg = app.load_config()
    Image = app.Image
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))

    for row, (light, fill) in enumerate([(True, LIGHT), (False, DARK)]):
        top = MARGIN + row * BAND
        canvas.paste(fill, (0, top, W, top + BAND))
        obj = widget(app, cfg, light)
        y = top + (BAND - PANEL_H) // 2
        for col, (pct, minutes, error) in enumerate(STATES):
            reset = None if minutes is None else (
                datetime.now(timezone.utc).astimezone() + timedelta(minutes=minutes))
            panel = app.TaskbarWidget._render(obj, PANEL, PANEL_H, pct, reset, error)
            canvas.alpha_composite(panel, (INSET + col * PANEL, y))

    canvas.convert("RGB").save(OUT)
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
