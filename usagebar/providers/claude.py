"""Claude Pro/Max limits, from the endpoint Claude Code's /usage reads, with
the OAuth token Claude Code keeps - plus promotional events.
"""

import json
import os
import re
import time
import traceback
from datetime import datetime, timezone

from .. import VERSION
from ..deps import requests
from ..source import POLL_MIN_SECONDS, Source
from ..usage import Usage, watched_limits
from ..util import log, parse_reset


CRED_PATH = os.path.expanduser(os.path.join("~", ".claude", ".credentials.json"))
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


# ---------------------------------------------------------------------------
# Usage data
# ---------------------------------------------------------------------------

LABELS = {
    "session": "Session (5h)",
    "weekly_all": "Weekly (all models)",
    "weekly_scoped": "Weekly (scoped)",
    "weekly_opus": "Weekly (Opus)",
}


def read_token():
    with open(CRED_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for section in ("claudeAiOauth", "claudeAiOAuth"):
        blob = data.get(section) or {}
        if blob.get("accessToken"):
            return blob["accessToken"]
    raise RuntimeError("no accessToken in credentials file")


def fetch_usage(with_events=False):
    """One poll. With `with_events`, the same request also asks for promotional
    grants - which the endpoint only reports to Claude Code, so that request
    identifies as the Claude Code installed here. It returns every usual field
    as well (only `spend` is skipped), so the event check costs no extra call."""
    u = Usage()
    u.with_events = with_events
    try:
        token = read_token()
        resp = requests.get(
            USAGE_URL,
            params={"cedar_ember": "1", "skip_spend": "1"} if with_events else None,
            headers={
                "Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
                "User-Agent": claude_code_user_agent() if with_events else "claude-usage-bar/" + VERSION,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            u.status = resp.status_code
            u.error = {
                401: "Signed out - run any Claude Code command",
                403: "Signed out - run any Claude Code command",
                429: "Rate limited by the usage API",
                500: "Usage service is having trouble",
                503: "Usage service is having trouble",
            }.get(resp.status_code, "Usage API error (HTTP %s)" % resp.status_code)
            log("usage http %s" % resp.status_code)
            return u
        data = resp.json()
        u.raw = data
        for item in data.get("limits") or []:
            kind = item.get("kind") or "unknown"
            label = LABELS.get(kind, kind.replace("_", " ").title())
            scope = item.get("scope") or {}
            model = (scope.get("model") or {}).get("display_name")
            if model:
                label = "Weekly (%s)" % model
            u.limits.append({
                "key": kind,
                "label": label,
                "percent": float(item.get("percent") or 0),
                "resets_at": item.get("resets_at"),
                "severity": item.get("severity") or "normal",
                "group": item.get("group") or kind,
                "active": bool(item.get("is_active")),
            })
        if not u.limits:
            for key, src in (("session", "five_hour"), ("weekly_all", "seven_day")):
                blob = data.get(src) or {}
                if blob.get("utilization") is not None:
                    u.limits.append({
                        "key": key,
                        "label": LABELS[key],
                        "percent": float(blob["utilization"]),
                        "resets_at": blob.get("resets_at"),
                        "severity": "normal",
                        "group": key,
                    })
        u.spend = data.get("spend")
        u.extra = data.get("extra_usage")
        u.breakdown = breakdown_rows(data)
        u.buckets = extra_buckets(data)
        if with_events:
            # Grants are a bonus on top of the usage numbers: if they cannot
            # be read, the numbers still count and events stays None (unknown).
            try:
                u.events = parse_events(data)
                u.events_checked = time.time()
            except Exception:
                log("events unreadable: %s" % traceback.format_exc())
        u.updated = datetime.now()
    except FileNotFoundError:
        u.error = "Not signed in to Claude Code"
    except requests.RequestException:
        u.error = "Offline"
    except Exception:
        u.error = "Something went wrong"
        log("fetch failed: %s" % traceback.format_exc())
    return u


# The endpoint reports several usage pools as top-level objects shaped like
# {"utilization": .., "resets_at": .., "locked_reason": ..}. Most are
# codenamed and null. five_hour and seven_day are the pools `limits` already
# describes and extra_usage is the paid overage shown on its own; everything
# else is surfaced as-is, because the names change.
_LIMIT_BUCKETS = ("five_hour", "seven_day", "extra_usage")


def extra_buckets(data):
    out = []
    for key, value in (data or {}).items():
        if key in _LIMIT_BUCKETS or not isinstance(value, dict) or "utilization" not in value:
            continue
        promotional = "promo" in key.lower()
        # seven_day_<model> pools repeat the model-scoped weekly limits that
        # `limits` already lists by name.
        if key.startswith("seven_day_") and not promotional:
            continue
        try:
            pct = float(value.get("utilization") or 0)
        except (TypeError, ValueError):
            continue             # one odd pool must not cost the whole poll
        out.append({
            "key": key,
            "label": key.replace("_", " ").capitalize(),
            "percent": pct,
            "resets_at": value.get("resets_at"),
            "locked_reason": value.get("locked_reason"),
            "promotional": promotional,
        })
    return out


def breakdown_rows(data):
    """Where this week's usage went, by product - only the fields shown, so
    nothing else the endpoint puts in a row ends up in the cache."""
    blob = (data or {}).get("seven_day_breakdown")
    rows = blob.get("rows") if isinstance(blob, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            pct = float(row.get("percent") or 0)
        except (TypeError, ValueError):
            continue
        key = str(row.get("key") or "")
        out.append({"key": key, "display_name": str(row.get("display_name") or key or "?"),
                    "percent": pct})
    return out


EVENT_CHECK_SECONDS = 3600       # grants change rarely; the endpoint rate-limits hard
USAGE_PAGE = "https://claude.ai/settings/usage"


def claude_code_user_agent():
    """The user agent of the installed Claude Code.

    The usage endpoint only reports promotional grants to Claude Code itself:
    asked by anything else it answers `"eligible": false, "ineligible_reason":
    "surface"`. So the one request that looks for grants identifies as the
    Claude Code on this machine - read from its install, not guessed.
    """
    version = None
    shape = re.compile(r"^\d+(\.\d+)+$")
    try:
        root = os.path.expanduser(os.path.join("~", ".local", "share", "claude", "versions"))
        names = [n for n in os.listdir(root) if shape.match(n)]
        if names:
            version = max(names, key=lambda n: tuple(int(x) for x in n.split(".")))
    except Exception:
        pass
    if not version:
        try:
            with open(os.path.expanduser(os.path.join("~", ".claude.json")), encoding="utf-8") as fh:
                seen = json.load(fh).get("lastReleaseNotesSeen")
            if isinstance(seen, str) and shape.match(seen):
                version = seen
        except Exception:
            pass
    return "claude-cli/%s (external, cli)" % (version or "2.1.0")


def _count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_events(data):
    """Grants that are live right now, from any program in the response.

    A program is any top-level object carrying a `grants` list (today that is
    `cedar_ember`, the limit-reset program). Reading them by shape rather than
    by codename means the next promotion shows up without a code change.
    """
    now = datetime.now(timezone.utc)
    events = []
    for program, value in (data or {}).items():
        if not isinstance(value, dict) or not isinstance(value.get("grants"), list):
            continue
        if value.get("eligible") is False:
            continue
        for grant in value["grants"]:
            if not isinstance(grant, dict):
                continue
            left = _count(grant.get("resets_left"))
            ends = parse_reset(grant.get("ends_at"))
            if grant.get("paused") or (left is not None and left <= 0):
                continue
            if ends is not None and ends <= now:
                continue
            clears = grant.get("clears")
            events.append({
                "program": program,
                "id": str(grant.get("id") or program),
                "label": str(grant.get("label") or program.replace("_", " ").capitalize()),
                "resets_left": left,
                "resets_total": _count(grant.get("resets_total")),
                "ends_at": grant.get("ends_at") if ends is not None else None,
                "clears": [str(c) for c in clears] if isinstance(clears, list) else [],
                "usable_now": grant.get("usable_now") is not False,
            })
    return events


def live_events(usage):
    """The cached grants that have not expired. The cache can outlive a grant
    when polls keep failing, so expiry is checked again at display time."""
    now = datetime.now(timezone.utc)
    out = []
    for event in (getattr(usage, "events", None) or []):
        ends = parse_reset(event.get("ends_at"))
        if ends is None or ends > now:
            out.append(event)
    return out


def describe_clears(clears):
    """What a limit reset puts back to full, in words."""
    names = {"five_hour": "5-hour", "seven_day": "weekly"}
    parts = [names[c] for c in clears if c in names]
    if not parts:
        return ""
    return " and ".join(parts) + (" limit" if len(parts) == 1 else " limits")


class ClaudeSource(Source):
    """Claude Pro/Max limits from the endpoint Claude Code's /usage reads,
    with the OAuth token Claude Code keeps - plus promotional events."""

    key, name = "claude", "Claude"
    missing_error = "Not signed in to Claude Code"

    def __init__(self, app):
        self.events_retry_at = 0.0       # a failed event check waits its turn
        self._event_backoff = 0
        Source.__init__(self, app)

    def fetch(self, events):
        return fetch_usage(events)

    def configured_interval(self):
        return self.cfg.get("refresh_seconds", POLL_MIN_SECONDS)

    def usage_page(self):
        return self.cfg.get("usage_page_url") or "https://claude.ai/settings/usage"

    def metric(self):
        return ((self.cfg.get("taskbar_widget") or {}).get("metric")
                or self.cfg.get("primary_metric") or "session")

    def events_enabled(self):
        return bool(self.cfg.get("check_events", True))

    def wants_events(self, manual):
        # Refresh re-checks events too, so a reset you have just used stops
        # being advertised straight away.
        return self.events_enabled() and (manual or self.event_poll_due())

    def event_poll_due(self):
        """One poll an hour doubles as the event check, so looking for grants
        never costs a request of its own. A failed event check backs off on its
        own clock, and the polls in between go out as plain usage requests - so
        a problem with the event check can never freeze the numbers."""
        if not self.events_enabled():
            return False
        now = time.time()
        if now < self.events_retry_at:
            return False
        age = now - float(self.usage.events_checked or 0)
        return self.usage.events is None or age >= EVENT_CHECK_SECONDS

    def _event_check_failed(self, result):
        self._event_backoff = min(max(self._event_backoff * 2, 300), EVENT_CHECK_SECONDS)
        self.events_retry_at = time.time() + self._event_backoff
        log("event check failed (%s); next in %ds"
            % (result.error or "unreadable grants", self._event_backoff))

    def accept(self, result):
        if result.with_events:
            if result.error or result.events is None:
                self._event_check_failed(result)
                if result.status not in (None, 429):
                    # Refused as the event check: that says nothing about
                    # usage, so ask again the plain way rather than show - or
                    # announce - an error that may be the event check's alone.
                    self._fetch(False)
                    return False
            else:
                self._event_backoff = 0
        return True

    def merge(self, result):
        fresh_events = result.with_events and not result.error and result.events is not None
        if fresh_events:
            if result.spend is None:          # the event poll skips `spend`
                result.spend = self.usage.spend
            before = [e["id"] for e in (self.usage.events or [])]
            if [e["id"] for e in result.events] != before:
                log("events: %s" % (", ".join(e["label"] for e in result.events) or "none"))
        elif self.events_enabled():
            result.events = self.usage.events
            result.events_checked = self.usage.events_checked

    def watched(self, n):
        return watched_limits(self.usage, n.get("metrics") or [n.get("metric", "session")])

    def limit_name(self, lim):
        return lim["label"]

    def signed_out_body(self, error):
        if error == self.missing_error:
            return "Claude Code is not signed in on this PC. Run claude and log in."
        if error.startswith("Signed out"):
            return "Claude Code's sign-in has expired. Run any claude command to refresh it."
        return None

    def notes(self):
        extra = self.usage.extra or {}
        if extra.get("is_enabled"):
            return ["Extra usage: %d%% of the monthly limit"
                    % round(float(extra.get("utilization") or 0))]
        return []
