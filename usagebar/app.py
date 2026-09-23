"""The app: providers, notifications, the tray icon, the menu, and main()."""

import ctypes
import ctypes.wintypes as wt
import faulthandler
import json
import os
import subprocess
import sys
import time
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime, timezone

from . import deps, paths
from . import tkui
from . import APP_NAME, VERSION
from .config import load_config
from .flyout import Flyout
from .providers import SOURCES, enabled_providers
from .providers.claude import describe_clears, live_events
from .render import render_cell, render_strip, segment_roles
from .toast import Toast
from .usage import load_notify_state, save_notify_state, shown_error, window_key
from .util import human_delta, log, parse_reset
from .widget import TaskbarWidget
from .win32 import IMAGE_ICON, LR_LOADFROMFILE, MF_CHECKED, MF_GRAYED, MF_SEPARATOR, MF_STRING, NIF_GUID, NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_SHOWTIP, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY, NIN_SELECT, NOTIFYICONDATA, NOTIFY_FOR_THIS_SESSION, PM_REMOVE, SM_CXSMICON, TPM_BOTTOMALIGN, TPM_RETURNCMD, TPM_RIGHTALIGN, TPM_RIGHTBUTTON, WM_COMMAND, WM_CONTEXTMENU, WM_DESTROY, WM_LBUTTONUP, WM_RBUTTONUP, WM_TRAY, WM_WTSSESSION_CHANGE, WNDCLASS, WNDPROC, WTS_SESSION_LOCK, WTS_SESSION_UNLOCK, guid_for, kernel32, shell32, user32, wtsapi32


CMD_DETAILS, CMD_REFRESH, CMD_CONFIG, CMD_RELOAD, CMD_WEB, CMD_STARTUP, CMD_LOG, CMD_QUIT = range(1, 9)
CMD_PROVIDER = 20                       # + index into SOURCES


def startup_lnk_path():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", "LLM Usage Bar.lnk")


def toggle_startup():
    lnk = startup_lnk_path()
    if os.path.exists(lnk):
        os.remove(lnk)
        return False
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    script = os.path.join(paths.APP_DIR, "llm_usage_bar.pyw")
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='\"%s\"';$s.WorkingDirectory='%s';"
        "$s.WindowStyle=7;$s.Description='LLM usage on the taskbar';$s.Save()"
        % (lnk, pyw, script, paths.APP_DIR)
    )
    subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                   creationflags=0x08000000, check=False)
    return os.path.exists(lnk)


class TrayApp(object):
    def __init__(self):
        self.cfg = load_config()
        self.use_guid = bool(self.cfg["icon"].get("use_guid", True))
        self.cfg_mtime = os.path.getmtime(paths.CONFIG_PATH) if os.path.exists(paths.CONFIG_PATH) else 0
        self.hicons = []
        self.registered = 0
        self.use_guid = bool(self.cfg["icon"].get("use_guid", True))
        self.icon_slot = 0
        self.state_path = paths.NOTIFY_STATE
        self.last_notified = load_notify_state(self.state_path)
        self.flyout = None
        self.widget = None
        self.toast = None
        self.locked = False
        self._deferred = []              # clicks and commands, for the pump to run
        self.sources = {}                # key -> Source, for the enabled ones
        self.sync_sources()

        os.makedirs(paths.ICON_CACHE, exist_ok=True)
        self.icon_size = user32.GetSystemMetrics(SM_CXSMICON) or 16

        self.hinst = kernel32.GetModuleHandleW(None)
        self.wndproc = WNDPROC(self._wndproc)
        wc = WNDCLASS()
        wc.lpfnWndProc = self.wndproc
        wc.hInstance = self.hinst
        wc.lpszClassName = "LLMUsageBarWnd"
        self.atom = user32.RegisterClassW(ctypes.byref(wc))
        self.wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.hwnd = user32.CreateWindowExW(0, "LLMUsageBarWnd", APP_NAME,
                                           0, 0, 0, 0, 0, None, None, self.hinst, None)
        # Told when the session locks and unlocks: nobody reads a locked screen,
        # so polling pauses until you are back.
        try:
            wtsapi32.WTSRegisterSessionNotification(self.hwnd, NOTIFY_FOR_THIS_SESSION)
        except Exception:
            log("no lock notifications: %s" % traceback.format_exc())
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
        first = self.active()[0]
        if first.key == "claude":
            primary = self.usage.percent(self.cfg["primary_metric"])
            sec_key = self.cfg.get("secondary_metric")
            secondary = self.usage.percent(sec_key) if sec_key else None
        else:
            primary, secondary = self.usage.percent(first.metric()), None
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
            path = os.path.join(paths.ICON_CACHE, "tray%d_%d.ico" % (self.icon_slot, k))
            img.save(path, format="ICO", sizes=[(size, size)])
            self.hicons.append(user32.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE))
        for handle in old:
            if handle:
                user32.DestroyIcon(handle)
        return self.hicons

    def _tooltip(self):
        """The template for Claude, then a line for each other provider."""
        lines = [self._claude_tooltip()] if "claude" in self.sources else []
        for src in self.active():
            if src.key == "claude":
                continue
            error = shown_error(src.usage)
            lim = src.usage.by_key(src.metric())
            lines.append("%s: %s" % (src.name, error) if error or not lim else
                         "%s \u00b7 %s: %d%%" % (src.name, lim["label"], round(lim["percent"])))
        return "\n".join(lines)[:127]

    def _claude_tooltip(self):
        u = self.sources["claude"].usage
        if shown_error(u):
            return "Claude usage: %s" % shown_error(u)
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
            return self.cfg["tooltip_template"].format(**values)
        except Exception:
            return "Claude %s%%" % values["primary"]

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
            self.widget.refresh(self.active())
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
        on = enabled_providers(self.cfg)
        for i, cls in enumerate(SOURCES):
            flags = MF_STRING | (MF_CHECKED if cls.key in on else 0)
            if on == [cls.key]:
                flags |= MF_GRAYED               # the last one stays on
            user32.AppendMenuW(menu, flags, CMD_PROVIDER + i, "Show %s usage" % cls.name)
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
            os.startfile(paths.CONFIG_PATH)
        elif cmd == CMD_RELOAD:
            self.reload_config()
        elif cmd == CMD_WEB:
            webbrowser.open(self.usage_page())
        elif CMD_PROVIDER <= cmd < CMD_PROVIDER + len(SOURCES):
            key = SOURCES[cmd - CMD_PROVIDER].key
            self.set_provider(key, key not in enabled_providers(self.cfg))
        elif cmd == CMD_LOG:
            if os.path.exists(paths.LOG_PATH):
                os.startfile(paths.LOG_PATH)
        elif cmd == CMD_STARTUP:
            on = toggle_startup()
            self.notify(APP_NAME, "Start with Windows: %s" % ("on" if on else "off"))
        elif cmd == CMD_QUIT:
            self.quit()

    def defer(self, fn, *args):
        """Run `fn` from the pump instead of here.

        Window procedures are called by whoever dispatches the message - and
        half the time that is Tk's own event loop, not our pump. Calling back
        into Tk from there (opening or destroying the flyout, a toast) leaves
        tkinter's record of the thread state empty, and the next Tk callback
        aborts the whole process with "Fatal Python error:
        PyEval_RestoreThread". Switching a provider on did exactly that, via
        the config reload destroying the flyout. So window procedures only
        queue work; the pump, an ordinary Tk callback, carries it out."""
        self._deferred.append((fn, args))

    def run_deferred(self):
        while self._deferred:
            fn, args = self._deferred.pop(0)
            try:
                fn(*args)
            except Exception:
                log("%s failed: %s" % (getattr(fn, "__name__", fn), traceback.format_exc()))

    def left_click(self):
        action = self.cfg.get("left_click", "flyout")
        if action == "flyout":
            self.toggle_flyout()
        elif action == "refresh":
            self.start_fetch(manual=True)
        elif action == "web":
            webbrowser.open(self.usage_page())

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, NIN_SELECT):
                self.defer(self.left_click)
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.defer(self.show_menu)
            return 0
        if msg == WM_COMMAND:
            self.defer(self.on_command, wparam & 0xFFFF)
            return 0
        if msg == self.wm_taskbar_created:
            self.registered = 0
            self._add_icon()
            return 0
        if msg == WM_WTSSESSION_CHANGE:
            if wparam == WTS_SESSION_LOCK:
                self.locked = True
            elif wparam == WTS_SESSION_UNLOCK:
                self.locked = False
                self.on_unlock()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # -- providers ---------------------------------------------------------
    def sync_sources(self):
        """Start or stop providers to match the config."""
        want = enabled_providers(self.cfg)
        for cls in SOURCES:
            if cls.key in want and cls.key not in self.sources:
                self.sources[cls.key] = cls(self)
                log("%s usage on" % cls.name)
            elif cls.key not in want and cls.key in self.sources:
                del self.sources[cls.key]
                log("%s usage off" % cls.name)

    def active(self):
        """The enabled providers, in display order."""
        return [self.sources[cls.key] for cls in SOURCES if cls.key in self.sources]

    @property
    def usage(self):
        """The first provider's numbers - what the tray icon draws."""
        return self.active()[0].usage

    def usage_page(self):
        return self.active()[0].usage_page()

    def set_provider(self, key, on):
        """Switch a provider on or off in config.json, keeping at least one."""
        want = enabled_providers(self.cfg)
        if not on and want == [key]:
            return
        try:
            with open(paths.CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
        entry = providers.get(key) if isinstance(providers.get(key), dict) else {}
        entry["enabled"] = bool(on)
        providers[key] = entry
        raw["providers"] = providers
        try:
            with open(paths.CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2)
        except Exception:
            log("could not save config: %s" % traceback.format_exc())
            return
        self.reload_config()

    # -- data --------------------------------------------------------------
    def maybe_poll(self):
        """Ticks often; each provider decides whether a poll of its own is due.
        Nobody reads a locked screen, so nothing is polled while it is locked."""
        if self.locked:
            return
        for src in self.active():
            src.maybe_poll()

    def start_fetch(self, manual=False):
        for src in self.active():
            src.start_fetch(manual)

    def on_unlock(self):
        for src in self.active():
            src.on_unlock()

    def apply_pending(self):
        changed = False
        for src in self.active():
            if src._pending is not None and src.apply_pending():
                changed = True
        if not changed:
            return
        self.update_icon()
        self.check_notifications()
        if self.flyout is not None:
            self.flyout.refresh(self.active())

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
        # Reaching a limit is always worth saying, whatever the list omits.
        thresholds = sorted(set(float(t) for t in (n.get("at") or [])) | {100.0})
        for src in self.active():
            signed_out = self._check_signed_out(n, src)
            if signed_out:
                due.append(signed_out)
            if not src.usage.error:
                for identity, lim in src.watched(n):
                    alert = self._notify_limit(identity, lim, thresholds, src)
                    if alert:
                        due.append(alert)
            if n.get("events", True):
                due.extend(self._grant_alerts(src))
        if due:
            if len(due) == 1:
                title, body = due[0][2], due[0][3]
            else:
                title = "LLM usage"
                body = "\n".join(alert[4] for alert in due)
            # Only count it as announced if it was actually shown: a toast held
            # back because you are in a game or a video is tried again next
            # poll rather than silently dropped.
            if self.notify(title, body) is not False:
                for alert in due:
                    self.last_notified[alert[0]] = alert[1]
        if self.last_notified != before:
            save_notify_state(self.state_path, self.last_notified)

    def _check_signed_out(self, n, src):
        """Numbers that silently stop updating are worse than a nudge. Once
        sign-in has been failing for a quarter of an hour, say so - once per
        outage. Only a successful poll ends an outage: going offline in the
        middle of one does not start the count again."""
        error = str(src.usage.error or "")
        key = "__signed_out__" if src.key == "claude" else "__signed_out__:" + src.key
        if not error:
            src.signed_out_since = None
            self.last_notified.pop(key, None)
            return None
        body = src.signed_out_body(error)
        if body is None:
            return None
        if src.signed_out_since is None:
            src.signed_out_since = time.time()
        if key in self.last_notified:
            return None
        if time.time() - src.signed_out_since < float(n.get("signed_out_after", 900)):
            return None
        title = "%s usage is not updating" % src.name
        return key, ("", 1), title, body, title

    def _grant_alerts(self, src):
        """One alert per grant, ever, on the first poll that can show it - held
        back while you are busy, it is simply due again next poll. A grant that
        cannot be used yet waits until it can."""
        out = []
        for event in live_events(src.usage):
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

    def _notify_limit(self, key, lim, thresholds, src):
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
        name = src.limit_name(lim)
        if level >= 100:
            title = "%s limit reached" % name
            # not .capitalize(): it lowercases the rest, turning "Sat" into "sat"
            body = (when[:1].upper() + when[1:]) if when else "No reset time reported."
        else:
            title = "%s usage %d%%" % (src.name, round(pct))
            body = "%s at %d%%%s" % (name, round(pct), (" - " + when) if when else "")
        # and a one-line form, for when several alerts share a toast
        line = ("%s - %s" % (title, when) if level >= 100 and when
                else title if level >= 100 else body)
        return key, (window, level), title, body, line

    def reload_config(self):
        self.cfg = load_config()
        self.cfg_mtime = os.path.getmtime(paths.CONFIG_PATH) if os.path.exists(paths.CONFIG_PATH) else 0
        self.icon_size = user32.GetSystemMetrics(SM_CXSMICON) or 16
        if self.flyout is not None:
            self.flyout.destroy()
            self.flyout = None
        if not self.tray_enabled():
            self.remove_icon()
        self.sync_sources()
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
            m = os.path.getmtime(paths.CONFIG_PATH)
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
        self.flyout.show(self.active())

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
            tkui.root.quit()
        except Exception:
            pass
        os._exit(0)


# ---------------------------------------------------------------------------
# Main loop: tkinter drives, Win32 messages are pumped from it
# ---------------------------------------------------------------------------

_instance_mutexes = []
_crash_file = None


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

    The old name's lock is held as well: a copy from before the rename (when
    this was Claude Usage Bar) must not run next to this one, whichever of
    the two starts first.
    """
    for name in ("Local\\LLMUsageBarMutex", "Local\\ClaudeUsageBarMutex"):
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, True, name)
        if not handle:
            continue                     # can't tell; better to run than not to
        _instance_mutexes.append(handle)
        # ERROR_ALREADY_EXISTS: another instance has it. Wait briefly for
        # ownership rather than guessing from a window it may not have yet.
        if ctypes.get_last_error() == 183 and \
                kernel32.WaitForSingleObject(handle, 1500) not in (0, 0x80):
            return False
    return True


def enable_crash_log():
    """A fatal error inside Python or Tcl aborts the process, and under
    pythonw its one-line explanation goes to a console that does not exist.
    faulthandler writes the stack of every thread to a file instead."""
    global _crash_file
    try:
        _crash_file = open(paths.CRASH_LOG, "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        pass


def main():
    moved = paths.migrate_legacy_files()         # before the first log line
    log("starting v%s (pid %d, %s)" % (VERSION, os.getpid(), sys.executable))
    if moved:
        log("renamed from Claude Usage Bar: moved %s" % ", ".join(moved))
    enable_crash_log()
    if not single_instance():
        log("another instance is running; exiting")
        return
    if not set_dpi_awareness():
        log("DPI awareness could not be set; coordinates may be scaled")

    tkui.root = tk.Tk()
    tkui.root.withdraw()
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
            tkui.root.after(interval(), run)
        tkui.root.after(interval() if first_ms is None else first_ms, run)

    def pump():
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        app.run_deferred()
        app.apply_pending()

    every("pump", 50, pump, first_ms=50)
    every("poll", 5000, app.maybe_poll, first_ms=500)
    every("config watch", 2000, lambda: app.reload_config() if app.config_changed_on_disk() else None)
    every("tray icons", 5000, app.ensure_icons)
    every("overlay", 500, lambda: app.widget.tick() if app.widget is not None else None,
          first_ms=300)
    every("clock", 30000, app.update_icon)
    tkui.root.mainloop()


def run():
    """The entry point (llm_usage_bar.pyw): start, unless the dependencies
    could not be imported - deps has logged why."""
    try:
        if deps.OK:
            main()
    except Exception:
        log("fatal: %s" % traceback.format_exc())
        raise
