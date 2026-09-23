"""The providers. Each one is a `Source` subclass in its own module;
adding one means writing that module and listing it in SOURCES.
"""

from .claude import ClaudeSource
from .ollama import OllamaSource


SOURCES = (ClaudeSource, OllamaSource)       # also the order they are shown in


def enabled_providers(cfg):
    """Keys of the providers switched on, in display order - never none."""
    chosen = cfg.get("providers") or {}
    keys = [cls.key for cls in SOURCES if bool((chosen.get(cls.key) or {}).get("enabled"))]
    return keys or [SOURCES[0].key]
