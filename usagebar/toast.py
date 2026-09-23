"""The app's own notification popup, for when there is no tray icon to balloon from."""

import ctypes
import ctypes.wintypes as wt
import tkinter as tk

from . import tkui
from .tkui import FLUENT, ui_scale
from .win32 import ABE_TOP, GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, round_window_corners, taskbar_info, user32, user_is_busy, windows_uses_light_theme


class Toast(object):
    """A notification for when there is no tray icon to hang a balloon on.

    Shell_NotifyIcon balloons need a registered, visible icon; running
    overlay-only used to mean the 80%/95% warnings went nowhere at all. This is
    the same Fluent surface as the flyout, above the corner the overlay sits in,
    and it fades itself away.
    """

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(tkui.root)
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
                tkui.root.after_cancel(self._after)
            except Exception:
                pass
        self._after = tkui.root.after(int(seconds * 1000), self.hide)
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
            tkui.root.after(16, lambda: self._fade(value, delta))
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
