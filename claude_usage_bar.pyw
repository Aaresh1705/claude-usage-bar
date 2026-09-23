"""The app's old name, kept so installs from before the rename keep working.

Their startup task, and the install.ps1 -Update they run, still start this
file. Running install.ps1 once moves the task over to llm_usage_bar.pyw, after
which nothing starts this file any more.
"""

import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_usage_bar.pyw"),
               run_name="__main__")
