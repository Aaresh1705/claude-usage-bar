"""Ollama cloud usage, from ollama.com, signed with the `ollama signin` key
(or an API key).
"""

import base64
import calendar
import hashlib
import os
import struct
import time
import traceback
from datetime import datetime

from .. import VERSION
from ..deps import requests
from ..source import Source
from ..usage import Usage
from ..util import log


OLLAMA_URL = "https://ollama.com"
OLLAMA_USAGE_PAGE = "https://ollama.com/settings/usage"
OLLAMA_KEY_PATH = os.path.expanduser(os.path.join("~", ".ollama", "id_ed25519"))


# Ollama ------------------------------------------------------------------------
#
# ollama.com reports cloud usage at GET /api/usage - undocumented; it is what
# the ollama.com settings page reads. It answers either an API key (Bearer) or
# a request signed the way the `ollama` CLI signs its own: the Ed25519 key pair
# `ollama signin` links to the account, over "METHOD,/path?ts=<unix seconds>".
# That signature is all this needs, so it is done here from RFC 8032 rather
# than pulling in a crypto library for one operation.

_ED_P = 2 ** 255 - 19
_ED_L = 2 ** 252 + 27742317777372353535851937790883648493
_ED_D = -121665 * pow(121666, _ED_P - 2, _ED_P) % _ED_P


def _ed_add(a, b):
    p = _ED_P
    A = (a[1] - a[0]) * (b[1] - b[0]) % p
    B = (a[1] + a[0]) * (b[1] + b[0]) % p
    C = 2 * a[3] * b[3] * _ED_D % p
    D = 2 * a[2] * b[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)


def _ed_mul(n, point):
    acc = (0, 1, 1, 0)
    while n > 0:
        if n & 1:
            acc = _ed_add(acc, point)
        point = _ed_add(point, point)
        n >>= 1
    return acc


def _ed_base():
    p = _ED_P
    y = 4 * pow(5, p - 2, p) % p
    x2 = (y * y - 1) * pow(_ED_D * y * y + 1, p - 2, p) % p
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p:
        x = x * pow(2, (p - 1) // 4, p) % p
    if x & 1:
        x = p - x
    return (x, y, 1, x * y % p)


_ED_B = _ed_base()


def _ed_encode(point):
    zi = pow(point[2], _ED_P - 2, _ED_P)
    x, y = point[0] * zi % _ED_P, point[1] * zi % _ED_P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def ed25519_sign(seed, message):
    """An Ed25519 signature (RFC 8032, 5.1.6) with the 32-byte private seed."""
    h = hashlib.sha512(seed).digest()
    a = (int.from_bytes(h[:32], "little") & ((1 << 254) - 8)) | (1 << 254)
    public = _ed_encode(_ed_mul(a, _ED_B))
    r = int.from_bytes(hashlib.sha512(h[32:] + message).digest(), "little") % _ED_L
    R = _ed_encode(_ed_mul(r, _ED_B))
    k = int.from_bytes(hashlib.sha512(R + public + message).digest(), "little") % _ED_L
    return R + ((r + k * a) % _ED_L).to_bytes(32, "little")


def ed25519_public(seed):
    h = hashlib.sha512(seed).digest()
    a = (int.from_bytes(h[:32], "little") & ((1 << 254) - 8)) | (1 << 254)
    return _ed_encode(_ed_mul(a, _ED_B))


def _ssh_string(buf, i):
    n = struct.unpack(">I", buf[i:i + 4])[0]
    return buf[i + 4:i + 4 + n], i + 4 + n


def read_openssh_ed25519(text):
    """(seed, public key blob) from an unencrypted OpenSSH ed25519 private key -
    the format `ollama` writes to ~/.ollama/id_ed25519."""
    body = "".join(l for l in text.strip().splitlines() if not l.startswith("-----"))
    raw = base64.b64decode(body)
    magic = b"openssh-key-v1\x00"
    if not raw.startswith(magic):
        raise ValueError("not an OpenSSH private key")
    cipher, i = _ssh_string(raw, len(magic))
    _, i = _ssh_string(raw, i)                  # kdf name
    _, i = _ssh_string(raw, i)                  # kdf options
    if cipher != b"none":
        raise ValueError("the key is passphrase protected")
    count = struct.unpack(">I", raw[i:i + 4])[0]
    public_blob, i = _ssh_string(raw, i + 4)
    private, _ = _ssh_string(raw, i)
    kind, j = _ssh_string(private, 8)           # after the two check ints
    if count != 1 or kind != b"ssh-ed25519":
        raise ValueError("not a single ed25519 key")
    _, j = _ssh_string(private, j)              # public key again
    secret, _ = _ssh_string(private, j)         # seed + public key
    return secret[:32], public_blob


def ollama_signature(key_text, method, uri):
    """The Authorization value the ollama CLI would send for `uri` (which
    carries its ?ts=), from the text of its private key file."""
    seed, public_blob = read_openssh_ed25519(key_text)
    signature = ed25519_sign(seed, ("%s,%s" % (method, uri)).encode())
    return "%s:%s" % (base64.b64encode(public_blob).decode(),
                      base64.b64encode(signature).decode())


def next_monthly_reset(day, now=None):
    """When Ollama's monthly credits next renew. They renew on the monthly
    anniversary of the subscription, which the API does not report - so the
    day comes from config.json (`reset_day`). None when it is not set."""
    try:
        day = int(day)
    except (TypeError, ValueError):
        return None
    if not 1 <= day <= 31:
        return None
    now = now or datetime.now().astimezone()

    def on(year, month):
        last = calendar.monthrange(year, month)[1]
        return now.replace(year=year, month=month, day=min(day, last),
                           hour=0, minute=0, second=0, microsecond=0)

    moment = on(now.year, now.month)
    if moment <= now:
        moment = on(now.year + 1, 1) if now.month == 12 else on(now.year, now.month + 1)
    return moment.isoformat()


# The windows ollama.com reports: plans since 31 Aug 2026 have one monthly
# credit budget; older Pro/Max subscriptions keep a 5-hour and a weekly one.
_OLLAMA_WINDOWS = (("session", "Session (5h)"), ("weekly", "Weekly"),
                   ("monthly", "Monthly credits"))


def parse_ollama_usage(data, reset_day=None, now=None):
    """(limits, spend) from /api/usage. `usage` there is a fraction of the
    plan's allowance (0.053 = 5.3%); a window that is absent is not metered,
    rather than shown as 0%. `spend` is the pay-as-you-go cost over the last
    four weeks, when there is any."""
    blob = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(blob, dict):
        raise ValueError("no limits in the Ollama response")
    known = dict(_OLLAMA_WINDOWS)
    order = [k for k, _ in _OLLAMA_WINDOWS] + sorted(k for k in blob if k not in known)
    limits = []
    for key in order:
        entry = blob.get(key)
        if not isinstance(entry, dict):
            continue
        try:
            fraction = float(entry.get("usage"))
        except (TypeError, ValueError):
            continue
        limits.append({
            "key": key,
            "label": known.get(key) or key.replace("_", " ").capitalize(),
            "percent": max(0.0, fraction * 100.0),
            "resets_at": next_monthly_reset(reset_day, now) if key == "monthly" else None,
            "severity": "normal",
            "group": key,
        })
    spend = None
    activity = data.get("activity")
    if isinstance(activity, dict):
        try:
            cost = float(activity.get("cost"))
            if cost > 0:
                spend = {"cost": cost}
        except (TypeError, ValueError):
            pass
    return limits, spend


def fetch_ollama_usage(settings):
    """One poll of ollama.com. An API key in config.json (or OLLAMA_API_KEY)
    wins; otherwise the request is signed with the `ollama signin` key."""
    u = Usage()
    api_key = str(settings.get("api_key") or os.environ.get("OLLAMA_API_KEY") or "").strip()
    path = "/api/usage"
    try:
        headers = {"Accept": "application/json", "User-Agent": "llm-usage-bar/" + VERSION}
        if api_key:
            uri = path
            headers["Authorization"] = "Bearer " + api_key
        else:
            with open(OLLAMA_KEY_PATH, "r", encoding="ascii") as fh:
                key_text = fh.read()
            uri = "%s?ts=%d" % (path, int(time.time()))
            headers["Authorization"] = ollama_signature(key_text, "GET", uri)
        resp = requests.get(OLLAMA_URL + uri, headers=headers, timeout=20)
        if resp.status_code != 200:
            u.status = resp.status_code
            if resp.status_code in (401, 403):
                u.error = ("Ollama API key refused" if api_key
                           else "Signed out - run ollama signin")
            else:
                u.error = {429: "Rate limited by ollama.com",
                           500: "ollama.com is having trouble",
                           503: "ollama.com is having trouble",
                           }.get(resp.status_code, "Ollama error (HTTP %s)" % resp.status_code)
            log("ollama usage http %s" % resp.status_code)
            return u
        u.limits, u.spend = parse_ollama_usage(resp.json(), settings.get("reset_day"))
        u.updated = datetime.now()
    except FileNotFoundError:
        u.error = "Not signed in to Ollama"
    except requests.RequestException:
        u.error = "Offline"
    except Exception:
        u.error = "Something went wrong"
        log("ollama fetch failed: %s" % traceback.format_exc())
    return u


class OllamaSource(Source):
    """Ollama cloud usage from ollama.com, signed with the `ollama signin` key
    (or an API key). Asked for gently: the budget is monthly and moves slowly,
    and the endpoint's own limits are unknown."""

    key, name = "ollama", "Ollama"
    poll_min, poll_idle, poll_max = 300, 900, 1800
    missing_error = "Not signed in to Ollama"

    def fetch(self, events):
        return fetch_ollama_usage(self.settings())

    def usage_page(self):
        return self.settings().get("usage_page_url") or OLLAMA_USAGE_PAGE

    def limit_name(self, lim):
        short = {"session": "session", "weekly": "weekly", "monthly": "monthly"}
        return "Ollama %s" % short.get(lim.get("key"), lim["label"].lower())

    def signed_out_body(self, error):
        if error == self.missing_error:
            return ("Ollama is not signed in on this PC. Run ollama signin, "
                    "or put an API key in config.json.")
        if error.startswith("Signed out") or "API key refused" in error:
            return ("ollama.com refused the sign-in. Run ollama signin again, "
                    "or check the API key in config.json.")
        return None

    def reset_hint(self, lim):
        return {"session": "Resets every 5 hours",
                "weekly": "Resets every 7 days",
                "monthly": "Renews on your billing day each month - set reset_day "
                           "in config.json to count down to it",
                }.get(lim.get("key"))

    def notes(self):
        spend = self.usage.spend or {}
        if spend.get("cost"):
            return ["Pay-as-you-go: $%.2f over the last 4 weeks" % float(spend["cost"])]
        return []
