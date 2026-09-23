"""LLM Usage Bar - live usage of your LLM subscriptions on the Windows 11 taskbar.

The app is the usagebar package beside this file; this is the script the
startup task runs. Run it with pythonw.exe (no console).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from usagebar.app import run  # noqa: E402

run()
