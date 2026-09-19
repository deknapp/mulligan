"""Where mulligan keeps downloads and caches: ``$MULLIGAN_CACHE``, else
``$XDG_CACHE_HOME/mulligan``, else ``~/.cache/mulligan`` — never the current
directory."""

from __future__ import annotations

import os
from pathlib import Path


def cache_dir(*parts: str) -> Path:
    base = os.environ.get("MULLIGAN_CACHE")
    if base:
        root = Path(base)
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mulligan"
    return root.joinpath(*parts)
