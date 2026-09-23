"""The details panel that opens from the widget."""

import ctypes
import ctypes.wintypes as wt
import os
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timezone

from . import paths
from . import tkui
from .deps import Image, ImageDraw, ImageTk
from .providers.claude import USAGE_PAGE, describe_clears, live_events
from .render import draw_text_centered, load_font
from .tkui import FLUENT, ui_scale
from .usage import shown_error
from .util import color_for, hex_to_rgba, human_delta, parse_reset, shade
from .win32 import ABE_TOP, round_window_corners, taskbar_info, user32, windows_accent_color, windows_uses_light_theme


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

        self.win = tk.Toplevel(tkui.root)
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
            available = set(tkfont.families(tkui.root))
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

    def refresh(self, sources):
        """Only rebuild when something actually changed: a full rebuild drops
        button hover states and flashes the panel."""
        if not self.visible:
            return
        if self.content_key(sources) == getattr(self, "_content_key", None):
            return
        self.render(sources)
        self.win.update_idletasks()
        self.win.geometry("%dx%d" % (self.win.winfo_reqwidth(), self.win.winfo_reqheight()))

    @staticmethod
    def content_key(sources):
        """Everything the panel shows, so refresh() rebuilds only on a change."""
        return tuple((src.key, usage.error, usage.updated,
                      tuple((l["label"], round(l["percent"], 2), l["resets_at"])
                            for l in usage.limits),
                      tuple((b["key"], b["percent"]) for b in (usage.buckets or [])),
                      tuple((r.get("key"), r.get("percent")) for r in (usage.breakdown or [])),
                      tuple((e["id"], e.get("resets_left"), e.get("usable_now"))
                            for e in live_events(usage)),
                      tuple(src.notes()))
                     for src, usage in ((src, src.usage) for src in sources))

    def render(self, sources):
        self._content_key = self.content_key(sources)
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

        several = len(sources) > 1
        header = tk.Frame(self.body, bg=colors["surface"])
        header.pack(fill="x")
        label(header, "Usage" if several else "%s usage" % sources[0].name,
              size=16, weight="bold", side="left")
        times = [src.usage.updated for src in sources if src.usage.updated]
        stamp = "Updated %s" % max(times).strftime("%H:%M") if times else "No data yet"
        label(header, stamp, size=12, color=colors["muted"], side="right")

        for i, src in enumerate(sources):
            if several:
                self._section_heading(src, colors, label, first=(i == 0))
            self._render_source(src, colors, content, label)

        divider = tk.Frame(self.body, bg=colors["divider"], height=1)
        divider.pack(fill="x", pady=(self.px(16), 0))

        footer = tk.Frame(self.body, bg=colors["surface"])
        footer.pack(fill="x", pady=(self.px(12), 0))
        self._button(footer, "Refresh", lambda: self.app.start_fetch(manual=True),
                     colors, accent=True).pack(side="left")
        if not several:                  # with several, each heading links its own
            self._button(footer, "Usage page",
                         lambda: webbrowser.open(sources[0].usage_page()),
                         colors).pack(side="left", padx=(self.px(8), 0))
        self._button(footer, "Settings", lambda: os.startfile(paths.CONFIG_PATH),
                     colors).pack(side="left", padx=(self.px(8), 0))

    def _section_heading(self, src, colors, label, first):
        """With more than one provider, each gets a heading that opens its own
        usage page."""
        if not first:
            tk.Frame(self.body, bg=colors["divider"], height=1).pack(
                fill="x", pady=(self.px(16), 0))
        row = tk.Frame(self.body, bg=colors["surface"])
        row.pack(fill="x", pady=(self.px(12 if first else 14), 0))
        label(row, src.name, size=14, weight="bold", side="left")
        link = label(row, "Usage page \u2197", size=12, color=self.accent(), side="right")
        link.configure(cursor="hand2")
        link.bind("<Button-1>", lambda e, url=src.usage_page(): webbrowser.open(url))

    def _render_source(self, src, colors, content, label):
        usage = src.usage
        light = colors is FLUENT["light"]
        error = shown_error(usage)
        if error:
            note = tk.Frame(self.body, bg=colors["surface"])
            note.pack(fill="x", pady=(self.px(10), 0))
            text = error
            if src.retry_at and time.time() < src.retry_at:
                text += " · retrying in %s" % human_delta(
                    datetime.fromtimestamp(src.retry_at, timezone.utc))
            label(note, text, size=12, color=colors["caution"], anchor="w", fill="x")

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
            hint = None
            if reset:
                fmt = "%a %d %b" if (reset - datetime.now(timezone.utc)).days >= 7 else "%a %H:%M"
                hint = "Resets %s · in %s" % (reset.strftime(fmt), human_delta(reset))
            else:
                hint = src.reset_hint(lim)
            if hint:
                label(self.body, hint, size=12, color=colors["muted"], anchor="w", fill="x",
                      pady=(self.px(4), 0)).configure(wraplength=content)

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

        for line in src.notes():
            label(self.body, line, size=12, color=colors["muted"], anchor="w", fill="x",
                  pady=(self.px(12), 0))

    def show(self, sources):
        cfg = self.app.cfg["flyout"] or {}
        self.scale = ui_scale()
        self.render(sources)
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
