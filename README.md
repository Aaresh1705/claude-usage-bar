# Claude Usage Bar

A live Claude usage indicator that sits on the right side of the Windows 11 taskbar,
next to the clock. Pure Python (`ctypes` + Win32 tray API + Pillow) — no extension,
no injection, nothing that breaks when Windows updates the taskbar.

It reads the same data `claude /usage` shows: your 5-hour session limit, your weekly
limit, and any model-scoped weekly limit, straight from
`https://api.anthropic.com/api/oauth/usage`, authenticated with the OAuth token
already stored in `~/.claude/.credentials.json`. Nothing else leaves the machine.

It can draw itself in two places, independently:

**Taskbar overlay** — a small always-visible readout in the taskbar's free left
corner, where the weather widget used to be. Turn Widgets off (Settings →
Personalization → Taskbar → Widgets) to clear the corner for it.

```
taskbar:  [15%  1h 30m ]                [start]  [search]   ...   13.20
           ^ number, reset countdown and a bar, blended into the taskbar
```

**Tray icons** — one or more icons next to the clock.

```
taskbar:  ... [OneDrive] [86] [wifi] [vol] [batt]  13.20
                          ^ colored pill = session %, thin strip = weekly %
```

### Why an overlay and not a real widget

Windows 11 has no way to put third-party content on the taskbar strip. The
weather readout belongs to Microsoft's widgets service; deskbands, the supported
extension point in Windows 10, were removed. Everything you see living on the
Windows 11 taskbar — now-playing bars and the like — is a borderless window drawn
over it, which is what this does. `widget/` holds the other route, a genuine
widgets-board provider, but that one can only ever appear inside the Win+W panel.

### Interaction

* **Hover** — tooltip with both percentages and the reset countdown.
* **Left click** — a Windows 11 style flyout: system surface colour, rounded
  corners (DWM), the accent colour on the primary button, Segoe UI Variable, and
  it fades in and closes when you click away. It follows the Windows light/dark
  setting on its own.
* **Right click** — menu: refresh, edit/reload config, open the web usage page,
  open the log, toggle "Start with Windows", quit.
* **Notification** when the session crosses 80% and 95% - once per threshold per
  reset window, never a repeat while you sit at the limit. With tray icons on
  that is a normal tray balloon; with the tray off there is no icon to hang one
  on, so the app draws its own Fluent toast above the corner instead. Neither
  appears while Windows says you are busy (a game, a call, a presentation).
* **Skull** instead of a number at 100%: the pill can only ever show two
  digits, so "100" used to render as "99". The skull is drawn by hand
  (`draw_skull`), not an emoji, so it stays crisp at 16px. Set
  `icon.skull_at` to another percentage, or `null` to keep the number.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Installs `pillow`/`requests` if missing, creates a Startup shortcut (runs hidden
under `pythonw.exe`, no console window), and starts it immediately.

Remove with `install.ps1 -Uninstall` (kills the process, deletes the shortcut,
keeps `config.json`).

If the icon lands in the tray overflow (the `^` chevron), drag it out once — Windows
remembers the position, or set it permanently in
Settings → Personalization → Taskbar → Other system tray icons.

## Configuration

Everything lives in `config.json`. The file is watched: **save it and the icon
re-renders within two seconds**, no restart needed. Delete the file to regenerate
the defaults. Unknown keys are ignored; missing keys fall back to the defaults, so
you can keep a minimal file with just your overrides.

### Top level

| Key | Meaning |
| --- | --- |
| `refresh_seconds` | Poll interval. 60 is comfortable; the endpoint is cheap but don't go below ~15. |
| `primary_metric` | What the big number/pill shows: `session`, `weekly_all`, `weekly_scoped`, or `max` (whichever limit is currently highest). |
| `secondary_metric` | What the thin strip shows. Same values, or `null` to drop it. |
| `left_click` | `flyout`, `refresh`, or `web`. Applies to the tray icons and the overlay. |
| `tray.enabled` | Draw the tray icons at all. Turn it off to run overlay-only. |
| `usage_page_url` | Where "Open usage page" goes. |
| `tooltip_template` | See placeholders below. |

### `icon`

| Key | Meaning |
| --- | --- |
| `style` | `bar_text` (default: number over a fill pill), `text_bar` (number above a bar), `bar` (bar only), `ring` (donut + number), `text` (number only, colored). |
| `segments` | Makes the indicator **wider than one square** — see "Going wider" below. |
| `segment_mode` | `cells` (default) or `slice`. |
| `count` | Overrides the length of `segments` (pads with `bar`). `0`/absent = use `segments` as written. |
| `reverse_segments` | Flip left-to-right order if Windows registers your icons backwards. |
| `label_format` | Text in the number cell. `{pct}`, `{secondary}`, plus any literal text — e.g. `"{pct}%"`. |
| `skull_at` | Percentage at which the number is replaced by a drawn skull. Default `100`; `null` keeps the number (which can only show two digits, so 100% reads as `99`). |
| `text_cell_style` | Style used for a `text` segment: `text` (default), `bar_text`, `ring`. |
| `cell_bar_thickness` | Bar height inside a `bar` segment, as a fraction of the icon. |
| `use_guid` | Stable per-segment identity so Windows remembers tray promotion. Default on. |
| `orientation` | `horizontal` or `vertical` — only affects `bar`. |
| `margin` | Pixels of transparent padding at the icon edge. |
| `corner_radius` | Pill/bar rounding in pixels. |
| `track_color` / `track_alpha` | The unfilled part of the bar. |
| `text_color`, `text_shadow` | The number. |
| `font_file` | Any file in `C:\Windows\Fonts` — `segoeuib.ttf`, `seguisb.ttf`, `consolab.ttf`, … |
| `font_scale` | Number height as a fraction of the available box. Raise to ~0.9 for a chunkier digit. |
| `bar_thickness`, `secondary_thickness`, `gap` | Fractions of the icon size. |
| `show_secondary`, `secondary_color` | The thin second-metric strip. |
| `ring_thickness` | Donut width for `style: ring`. |
| `supersample` | Anti-aliasing factor (8 = render at 8× then downscale). Lower it only if CPU matters. |

### `taskbar_widget`

The overlay. It is a **layered window**: `UpdateLayeredWindow` hands DWM a bitmap
with per-pixel alpha, so nothing paints a background at all — the real taskbar,
with its acrylic tint and the gradient it picks up from your wallpaper, simply
shows through. That is a deliberate change from the first version, which sampled
the taskbar's colour with `GetPixel` and filled a rectangle with it: the taskbar
is not one colour (measured here: it varies by ~7 RGB levels across its own
width), so a flat fill can only ever match at the point it was sampled.

Two more consequences of the layered window: updates arrive as one composited
frame, which is why it no longer flickers, and the whole rectangle is painted at
alpha 1 — invisible, but enough for `UpdateLayeredWindow`'s alpha hit-testing to
route clicks to the widget rather than through it.

Z-order is held by answering `WM_WINDOWPOSCHANGING` (forcing `hwndInsertAfter`
back to `HWND_TOPMOST`) instead of calling `SetWindowPos` on a half-second timer.
The timer version was what made the widget blink when a menu opened or a program
launched. A `SetWindowPos` remains as a safety net, every ten seconds.

| Key | Meaning |
| --- | --- |
| `enabled` | Master switch. |
| `metric` | Which limit to show. `null` = whatever `primary_metric` is. |
| `corner` | `left` (where Widgets used to be) or `right` (before the tray). |
| `width` | Width in logical pixels at 100% DPI; scaled with the taskbar. |
| `offset` | `[x, y]` nudge from the corner, also DPI-scaled. |
| `padding` | Gap above and below, so it doesn't touch the taskbar edges. |
| `background` | `transparent` (default) draws straight onto the taskbar. A hex colour paints a pill behind the content instead. |
| `background_alpha`, `corner_radius` | Only used when `background` is a colour. |
| `text_color` / `muted_color` | `auto` follows the Windows theme. |
| `show_bar`, `bar_height` | The progress bar under the countdown. |
| `show_reset` | The reset countdown. Drop it to save width. |
| `hide_on_fullscreen` | Get out of the way of games and full-screen video. |
| `fullscreen_grace_ms` | How long to wait after fullscreen ends before coming back (default 900). Without it the overlay reappears while the desktop is still repainting and is briefly the only thing on screen. |
| `stale_opacity` | Opacity for numbers that stopped being refreshed - rate limited, offline, signed out (default 0.55). |
| `fade_ms` | Fade-in when it reappears (default 160). |
| `supersample` | Render scale for the text; 3 is plenty. |

Severity colours follow your `thresholds` but are drawn from the Fluent palette,
so they stay legible on both a light and a dark taskbar. At 100% the number is
replaced by the same drawn skull the tray uses.

Fullscreen detection asks Windows itself (`SHQueryUserNotificationState`, the
signal that also holds back system toasts) and additionally compares the
foreground window to its monitor — and only to *our* monitor, so a video playing
full-screen on a second display no longer blanks the overlay on the first.

**When it goes away on purpose.** The Start menu, Search and Task View are shell
surfaces that Windows composites above every ordinary window, topmost or not, so
the overlay is hidden while one of them is open and returns when it closes
(measured: back within 0.35-0.8s). The taskbar stays visible underneath them,
which makes it look as though only the widget vanished. Nothing an app without
UIAccess can do about that - and drawing on top of the Start menu would be the
wrong behaviour anyway. Ordinary interactions do *not* hide it: clicking an empty
part of the taskbar, the desktop, another window, the notification centre, quick
settings, or this app's own menu and flyout all leave it in place.

If the widget ever does end up behind the taskbar in the window z-order, a
read-only check each tick notices and puts it back within half a second, rather
than waiting for the ten-second safety re-assert.

**Limits worth knowing.** With the taskbar centred, the left corner is free until
you have a lot of windows open — Windows will happily slide app buttons under the
overlay, since nothing reserves that space. Move it with `corner`/`offset` if that
bites. A vertical taskbar isn't supported (the overlay hides itself). And because
this leans on the taskbar's shape, a Windows update that reworks the taskbar can
require a fix here — the tray icons are the fallback that can't break that way.

### Going wider than a square

A single tray icon is fixed square by Windows (`SM_CXSMICON`: 16px at 100% DPI,
24px at 150%) — there is no API to request a wider slot. The way around it is to
register **several tray icons** and treat them as one indicator. `segments` is the
list of what each icon draws, left to right:

```json
"icon": {"segments": ["text", "bar", "bar", "bar"], "reverse_segments": true}
```

gives `92 ▬ ▬ ▬` — the number, then a three-cell gauge where each cell owns 33% of
the range and fills in turn, like signal strength. Five bars (currently configured)
put each cell at 20%: 0-20, 20-40, 40-60, 60-80, 80-100. Roles:

| Role | Draws |
| --- | --- |
| `text` | The percentage (styled by `text_cell_style`). |
| `bar` | One cell of the primary gauge; cells split the 0-100 range evenly. |
| `secondary` | A full bar of the secondary metric in `secondary_color`. |
| `ring` | A donut for the primary metric. |
| `blank` | Transparent spacer. |

Up to 8 segments. Notes:

* Windows 11 pads between tray cells, so segments read as a **dashed** gauge, not a
  continuous bar. That is why `cells` is the default — each cell is self-contained,
  so the gaps look deliberate.
* `"segment_mode": "slice"` instead renders one continuous bar and cuts it into
  `count` pieces. It gives you a genuinely long single bar, but the taskbar padding
  cuts visible notches through it (and through any text crossing a boundary).
* Windows may register the icons right-to-left; if the order comes out mirrored,
  set `"reverse_segments": true`.
* Each segment occupies a real tray slot, and you can drag them apart or into the
  overflow. Six segments (number + five bars) is comfortable; eight is the cap.
* **Changing the segment count re-registers the icons, and Windows 11 hides new tray
  icons by default** - they land in the `^` overflow. Re-run `install.ps1`: it flips
  `IsPromoted` for every entry of ours under `HKCU\Control Panel\NotifyIconSettings` and restarts the app. You can also
  drag them out by hand, or use Settings -> Personalization -> Taskbar -> Other
  system tray icons.
* Each segment registers a stable GUID (`use_guid`, on by default), so once promoted
  it stays promoted across restarts. Set `"use_guid": false` to fall back to plain
  window-handle identity if a registration ever gets stuck.

### `thresholds`

A list of `{at, color}` — the fill color for the primary metric, applied at or above
each percentage. Add as many stops as you like:

```json
"thresholds": [
  {"at": 0,  "color": "#3FB950"},
  {"at": 50, "color": "#9BD65C"},
  {"at": 75, "color": "#D29922"},
  {"at": 90, "color": "#F85149"}
]
```

### `tooltip_template`

`\n` for line breaks (tray tooltips cap at 127 characters). Placeholders:

`{primary}` `{primary_label}` `{primary_reset_short}` `{primary_reset_in}`
`{secondary}` `{secondary_label}` `{secondary_reset_short}` `{secondary_reset_in}`
`{updated}`

### `notifications`

`{"enabled": true, "at": [80, 95], "metric": "session"}` — a balloon fires once per
threshold per limit window, and re-arms when the window resets.

### `flyout`

| Key | Meaning |
| --- | --- |
| `width` | Panel width in logical pixels; scaled for DPI. |
| `theme` | `auto` follows the Windows app theme, or force `light` / `dark`. |
| `accent` | `auto` uses your Windows accent colour for the primary button, or a hex colour. |
| `corner_offset` | `[x, y]` distance from the work-area corner. |
| `close_on_focus_loss` | Close when you click elsewhere, like a system flyout. |

Colours inside the panel come from the Windows 11 Fluent palette rather than
`config.json`, so the panel matches the OS in either theme. The severity colours
still track the `thresholds` you configure — the ranks map onto the Fluent
success / caution / critical colours.

## Recipes

**Minimal white number, no bars**
```json
{"icon": {"style": "text", "show_secondary": false, "font_scale": 0.95}}
```

**Track the weekly limit instead, red early**
```json
{"primary_metric": "weekly_all", "secondary_metric": "session",
 "thresholds": [{"at": 0, "color": "#3FB950"}, {"at": 40, "color": "#F85149"}]}
```

**Wide six-cell gauge (what is running now)**
```json
{"icon": {"segments": ["text", "bar", "bar", "bar", "bar", "bar"],
          "reverse_segments": true}}
```

**Two icons: number, then the weekly bar**
```json
{"icon": {"segments": ["text", "secondary"], "label_format": "{pct}%"}}
```

**Whatever is closest to the limit, ring style**
```json
{"primary_metric": "max", "icon": {"style": "ring", "ring_thickness": 0.28}}
```

## Notes

* Only one instance runs. The lock is a per-session named mutex, and
  `ERROR_ALREADY_EXISTS` is only honoured when the other instance's window
  actually answers — a leftover lock can't stop the app from starting again.
* Every launch writes a `starting (pid …)` line to the log, so "it didn't come
  back after a reboot" is answerable: a line means it started (look for what
  followed), no line means Windows never launched it (check the Startup shortcut
  and Task Manager → Startup apps).
* Dependencies are imported with a retry loop for the first minute — at logon the
  profile or site-packages can briefly be unavailable — and a failure is logged
  instead of killing the process silently.
* Tray registration is retried every 5 seconds until every segment sticks;
  Explorer often refuses icons for a while right after logon.
* If the token expires, the icon keeps the last good numbers and the tooltip says
  `auth` — running any Claude Code command refreshes the credentials file and the
  next poll picks it up automatically.
* Network blips show `offline` without wiping the displayed numbers, and the last
  good figures are cached to `.usage_cache.json`, so a restart shows numbers
  immediately instead of an empty panel.
* An HTTP 429 from the usage endpoint backs the poller off (2 minutes, doubling
  up to 30) instead of retrying every minute; the overlay keeps showing the last
  figures and the flyout says when the next attempt is. **Refresh** ignores the
  backoff.
* The icon re-registers itself if Explorer restarts; the overlay re-reads the
  taskbar's geometry twice a second, so it follows moves, resizes and DPI changes.
* Notifications fire once per threshold per reset window — sitting at 100% does
  not re-notify, and the counter resets when the window rolls over.
* Errors go to `claude_usage_bar.log` next to the script (right click → Open log).
  Every launch, every overlay hide/show (with its reason) and every failing timer
  is recorded there, so "it vanished and I don't know why" is answerable after
  the fact.
* Every timer chain is individually guarded. A Tk `after` callback that raises
  kills its chain silently, and under `pythonw` the traceback goes nowhere - one
  unguarded exception used to be enough to stop polling, clicks or config reload
  while the app kept running and looking healthy.
* Numbers that stopped being refreshed are dimmed rather than shown as if live,
  and the on-disk cache is ignored once it is more than 12 hours old.
* A window that is merely maximised is no longer mistaken for a fullscreen one -
  which used to hide the overlay permanently for anyone with an auto-hiding
  taskbar, since the work area then equals the whole monitor.
* `/api/oauth/usage` is the endpoint Claude Code's own `/usage` uses. It is not a
  documented public API, so treat the shape as something that can change; the parser
  falls back to the older `five_hour` / `seven_day` fields if the `limits` array
  disappears.

## Files

| File | |
| --- | --- |
| `claude_usage_bar.pyw` | The whole app. |
| `config.json` | Your settings, hot-reloaded. |
| `install.ps1` | Install / uninstall. |
| `preview.png` | Style sheet rendered at 16px and 24px. |
| `preview_cells.png` | Segmented-gauge preview. |
| `.icons/` | Generated `.ico` files (throwaway). |
