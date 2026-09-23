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
  (`draw_skull`), not an emoji, so it stays crisp at 16px. It follows the
  Windows theme - near-black on light, near-white on dark - rather than the
  threshold colour. Set `icon.skull_at` to another percentage, or `null` to
  keep the number, and `icon.skull_color` to pin the colour.

## Install

Two ways, same app. A clone updates with one command; the exe needs nothing
installed on the machine.

Either way the machine has to be signed in to Claude Code (run `claude` once),
because the usage figures come from the OAuth token in
`~/.claude/.credentials.json`. And the taskbar's left corner has to be free:
turn the Widgets button off in **Settings → Personalization → Taskbar**.

### From a clone (recommended)

Needs Python 3 (`winget install Python.Python.3.12` if it is missing).

```powershell
git clone https://github.com/Aaresh1705/claude-usage-bar.git
cd claude-usage-bar
powershell -ExecutionPolicy Bypass -File install.ps1
```

That installs `pillow`/`requests` if they are missing, writes a `config.json`
with the defaults, registers a **scheduled task** that starts it ten seconds
after you sign in, and starts it now.

It is a task rather than a shortcut in the Startup folder for a reason. The
Startup folder is the last thing the shell gets to: measured on the machine this
was written on, the app launched **170 seconds after boot**, queued behind Teams,
OneDrive, Spotify and a Java updater - long enough that it looks like it never
started at all. The task does not wait in that queue, and it retries three times
if it fails. Use `-UseStartupFolder` if you would rather have the old shortcut,
and the installer falls back to it by itself if your machine's policy forbids
registering tasks.

To update later:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Update
```

which pulls and restarts. Your `config.json` is not tracked, so it survives
updates untouched.

### From the exe

`build.ps1` produces `dist\ClaudeUsageBar.exe` (~19 MB, Python and all the
libraries inside it). Copy that one file to another PC, put it in a folder of
its own, and run it. `install.ps1` next to the exe will make it start with
Windows.

The exe keeps `config.json`, the log and the cache beside itself. If you put it
somewhere read-only, it uses `%LOCALAPPDATA%\claude-usage-bar` instead.

**Corporate PCs:** a self-built exe is unsigned, and a machine with Windows
Defender Application Control enforced will refuse to run it with "Access is
denied" no matter where you put it (check with
`(Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard).CodeIntegrityPolicyEnforcementStatus`
— `2` means enforced). Use the clone route there: Python is signed, so it runs.

### Removing it

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
```

Stops it and removes the scheduled task (and the Startup shortcut, if you used
one). `config.json` and the log stay.

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
| `check_events` | Look for promotional grants once an hour (see *Events*). `false` never sends the request that identifies as Claude Code. Default `true`. |
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
| `skull_color` | Colour of that skull. Default `"auto"`: near-black (`#1A1A1A`) under the light Windows theme, near-white (`#F2F2F2`) under the dark one, so it reads against the taskbar instead of against the threshold colour. Give a hex colour to pin it. |
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

The overlay is a **layered window that is a child of the taskbar** - created as
a popup and handed to `Shell_TrayWnd` with `SetParent` (Windows refuses to create
it as a child outright). That one decision does all the work: the widget is
exactly as visible as the taskbar, no more and no less. Whatever covers the
taskbar covers the widget - a fullscreen video, Task View - and whatever leaves the
taskbar alone leaves the widget alone: clicking the taskbar, opening Start, an
auto-hiding taskbar sliding away. None of that is detected in code; Windows does
it, because the widget is part of the taskbar's own window tree.

*Layered* (`UpdateLayeredWindow`, per-pixel alpha) means nothing paints a
background - the real taskbar, acrylic tint and all, shows through around the
glyphs, and each frame lands as one composited update, so nothing flickers.

Earlier versions were a topmost window floating over the taskbar. That version
fought the shell: clicking the taskbar demoted it (a ~300ms blink), the Start
menu hid it, and it needed its own fullscreen detection and z-order polling to
paper over both. All of that is gone.

The one thing a child inherits is its parent's fate: an Explorer restart destroys
the taskbar and its children with it. The tick notices within half a second and
rebuilds, backing off if the shell is not back yet.

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
| `stale_opacity` | Opacity for numbers that have stopped being refreshed (default 0.7). Judged by age, not by whether the last poll failed - the endpoint rate-limits often and a figure from a minute ago is still worth showing at full strength. |
| `supersample` | Render scale for the text; 3 is plenty. |

Severity colours are the ones in `thresholds` (vivid green, amber and red by
default). At 100% the number is replaced by the same drawn skull the tray
uses, inked in the widget's own text colour so it follows the Windows theme.

Fullscreen detection asks Windows itself (`SHQueryUserNotificationState`, the
signal that also holds back system toasts) and additionally compares the
foreground window to its monitor — and only to *our* monitor, so a video playing
full-screen on a second display no longer blanks the overlay on the first.

**What covers it.** Exactly what covers the taskbar: a fullscreen app or video,
and Task View. Start, Search, the notification centre and quick settings leave the
taskbar visible, so they leave the widget visible too.

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

```json
{"enabled": true, "at": [80, 95, 100],
 "metrics": ["session", "weekly_all", "weekly_scoped"],
 "events": true, "signed_out_after": 900}
```

| Key | Meaning |
| --- | --- |
| `at` | Percentages to warn at. Reaching 100% is always announced ("Session (5h) limit reached"), whether or not it is listed. |
| `metrics` | Which limits to watch: `session`, `weekly_all`, `weekly_scoped` (every model-scoped weekly limit, each on its own), or `max` (whichever is highest). The old single `"metric"` key still works. |
| `events` | Announce a live promotion (see *Events*) once, when it can be used. |
| `signed_out_after` | Seconds of refused sign-in (or no Claude Code sign-in on this PC at all) before one "Claude usage is not updating" nudge. |

The rules, all covered by `tests/test_notifications.py`:

* **Once per threshold per limit window.** Never twice for the same level, never
  again for a level already passed, not again when usage dips below and back.
* **A limit reset re-arms them.** Inside one window usage only falls to under half
  the lowest threshold when the limit was reset early - a redeemed limit reset -
  so running out again after that is announced again.
* **A window is its reset time, rounded to the minute.** The API reports the same
  reset instant with different microseconds on every request
  (`12:10:00.165535`, then `12:10:00.134418`). Comparing the raw strings made
  every poll look like a new window, so an hour at 100% produced 60
  notifications. Rounded, it produces one.
* **Remembered across restarts** in `.notify_state.json`, so rebooting at 100%
  does not announce it again.
* **Everything due on one poll shares a toast** - several limits, or a limit and
  a new event - instead of one replacing the other.
* **Held back, not dropped.** While Windows says you are busy (a game, a
  fullscreen video, a presentation) nothing is shown - and nothing is marked as
  shown, so it appears once you are back.
* Errors and rate limiting never trigger anything, however they flap. The one
  exception is sign-in: refused for `signed_out_after` seconds, you get a single
  nudge per outage - and going offline in the middle of it does not start a new
  one.

### Events

The usage endpoint also reports promotional **grants** - right now, the one from
the Claude Opus 5.5 launch: *one usage-limit reset for Pro and Max, which puts
the 5-hour and weekly limits back to full, usable until 22 Oct 2026*. While one
is live the widget shows a small amber sparkle in its corner, the flyout opens
with a card saying what it is, how many are left, what it clears and when it
expires (with a button to Settings > Usage, where you redeem it), and a toast
announces it once.

Two details worth knowing:

* **The endpoint only tells Claude Code.** Asked by anything else it answers
  `"eligible": false, "ineligible_reason": "surface"`. So one poll an hour - the
  only request that does this - asks for grants (`?cedar_ember=1`) using the user
  agent of the Claude Code installed on this machine, read from
  `~/.local/share/claude/versions`. That poll carries the usual usage data too, so
  checking for events never costs an extra request against a rate limit that is
  already tight.
* **Grants are read by shape, not by name.** Any program in the response that
  carries a `grants` list is picked up, so the next promotion appears without a
  code change. The app only displays grants; it never redeems one.
* **The event check can never hold up the numbers.** If that poll fails, is
  refused, or returns grants it cannot read, the usage is still used (or asked
  for again the plain way straight away), and the event check backs off on its
  own clock - 5 minutes, doubling to an hour - while ordinary polls carry on.
  **Refresh** re-checks events too, so a reset you have just used stops being
  advertised at once. `check_events: false` turns the whole thing off.

The flyout also shows **where this week's usage went** (Claude Code, chats,
Cowork, …) as a split bar, from the endpoint's weekly breakdown, and any other
usage pool the endpoint reports once it is actually in use.

`taskbar_widget.show_events` (default `true`) and `taskbar_widget.event_color`
(default `#F59E0B`) control the sparkle.

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
  back after a reboot" is answerable. A line means it started - compare its
  timestamp with `(Get-CimInstance Win32_OperatingSystem).LastBootUpTime` to see
  how long the shell took to get to it. No line at all means Windows never
  launched it: check `Get-ScheduledTask 'Claude Usage Bar'`.
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
* Numbers are dimmed once they stop being refreshed for real (older than five
  minutes, or three polls, whichever is longer) rather than on the first failed
  poll, and the on-disk cache is ignored once it is more than 12 hours old.
* There is no fullscreen detection any more: as a child of the taskbar the widget
  is hidden by exactly what hides the taskbar, so there is nothing to detect.
* `/api/oauth/usage` is the endpoint Claude Code's own `/usage` uses. It is not a
  documented public API, so treat the shape as something that can change; the parser
  falls back to the older `five_hour` / `seven_day` fields if the `limits` array
  disappears.

## Files

| File | |
| --- | --- |
| `claude_usage_bar.pyw` | The whole app. |
| `config.json` | Your settings, hot-reloaded. Not tracked by git: it is written from the defaults on first run, so it survives updates. |
| `install.ps1` | Install, update (`-Update`), uninstall (`-Uninstall`). Registers the logon task; `-UseStartupFolder` for the old shortcut. |
| `build.ps1` | Builds the standalone `dist\ClaudeUsageBar.exe`. |
| `make_icon.py` | Draws `assets\ClaudeUsageBar.ico` for the exe and the shortcut. |
| `tests/test_notifications.py` | The notification and event rules, driven through the real code. `python tests/test_notifications.py`. |
| `make_preview.py` | Redraws `preview.png` by calling the widget's own renderer, so the README picture cannot drift from the app. |
| `preview.png` | The widget at 14%, 72%, 96%, 100% and rate-limited, light and dark. |
| `assets/` | The app icon. |
| `LICENSE` | MIT. |
| `.icons/` | Generated `.ico` files (throwaway). |

## Licence

MIT - see [LICENSE](LICENSE).
