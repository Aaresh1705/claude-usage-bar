# Claude Usage Bar

A live Claude usage indicator that sits on the right side of the Windows 11 taskbar,
next to the clock. Pure Python (`ctypes` + Win32 tray API + Pillow) — no extension,
no injection, nothing that breaks when Windows updates the taskbar.

It reads the same data `claude /usage` shows: your 5-hour session limit, your weekly
limit, and any model-scoped weekly limit, straight from
`https://api.anthropic.com/api/oauth/usage`, authenticated with the OAuth token
already stored in `~/.claude/.credentials.json`. Nothing else leaves the machine.

```
taskbar:  ... [OneDrive] [86] [wifi] [vol] [batt]  13.20
                          ^ colored pill = session %, thin strip = weekly %
```

* **Hover** — tooltip with both percentages and the reset countdown.
* **Left click** — flyout with every limit, a bar each, and reset times.
* **Right click** — menu: refresh, edit/reload config, open the web usage page,
  open the log, toggle "Start with Windows", quit.
* **Balloon notification** when the session crosses 80% and 95% - once per
  threshold per reset window, never a repeat while you sit at the limit.
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
| `left_click` | `flyout`, `refresh`, or `web`. |
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

`width`, `background`, `foreground`, `muted`, `accent`, `track_color`,
`font_family`, `font_size`, `title_size`, `corner_offset` (`[x, y]` from the
bottom-right of the work area), `close_on_focus_loss`.

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
* Network blips show `offline` in the tooltip without wiping the displayed numbers.
* The icon re-registers itself if Explorer restarts.
* Notifications fire once per threshold per reset window — sitting at 100% does
  not re-notify, and the counter resets when the window rolls over.
* Errors go to `claude_usage_bar.log` next to the script (right click → Open log).
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
