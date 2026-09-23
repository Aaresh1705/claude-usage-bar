"""The third-party imports (requests, Pillow), retried for a minute.

At logon these can fail for reasons that clear themselves seconds later
(roaming profile or OneDrive still mounting, antivirus holding site-packages).
As a plain import that was a silent death with nothing in the log; here every
failure is recorded. Importing this module does the loading; `OK` says
whether it worked.
"""

import time

from .util import log

requests = Image = ImageDraw = ImageFont = ImageTk = ImageChops = None


def _load(attempts=20, delay=3.0):
    global requests, Image, ImageDraw, ImageFont, ImageTk, ImageChops
    last = ""
    for attempt in range(attempts):
        try:
            import requests as _requests
            from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
            from PIL import ImageTk as _ImageTk
            from PIL import ImageChops as _ImageChops
        except Exception as exc:
            last = repr(exc)
            time.sleep(delay)
            continue
        requests, Image, ImageDraw, ImageFont = _requests, _Image, _ImageDraw, _ImageFont
        ImageTk, ImageChops = _ImageTk, _ImageChops
        if attempt:
            log("dependencies imported after %d retries" % attempt)
        return True
    log("fatal: cannot import dependencies: %s" % last)
    return False


OK = _load()
