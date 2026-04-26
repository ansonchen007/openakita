"""pytest helpers for the ppt-maker plugin.

The plugin tree contains a top-level `plugin.py`; clearing the import cache
prevents pytest from reusing another plugin's module named `plugin`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"


@pytest.fixture(autouse=True)
def isolate_plugin_imports() -> None:
    sys.path.insert(0, str(SRC_ROOT))
    sys.path.insert(0, str(PLUGIN_ROOT))
    sys.modules.pop("plugin", None)
    yield
    sys.modules.pop("plugin", None)
    for path in (str(PLUGIN_ROOT), str(SRC_ROOT)):
        try:
            sys.path.remove(path)
        except ValueError:
            pass

