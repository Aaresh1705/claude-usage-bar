"""Shared tkinter pieces: the root window, the Fluent palette, UI scale."""

import ctypes
import ctypes.wintypes as wt

from .win32 import MONITOR_DEFAULTTONEAREST, taskbar_info, user32

root = None          # the Tk root, created by app.main()


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
