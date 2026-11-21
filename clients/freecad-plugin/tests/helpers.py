"""Shared test helpers — module loading and sys.modules mocking.

Adopted from upstream CadAgent v1.4.1 tests/helpers.py (T8.8, ADR-004 §5),
trimmed to what the sonarlink test suite uses: ``load_tools_module`` /
``reload_config`` are upstream-only (agent/tools.py and core/config.py were
deliberately not migrated — see ADR-004 §2.7 / §5.4).
"""
from __future__ import annotations

import importlib.util
import os
import sys

# Source root for spec-loading: .../clients/freecad-plugin/SonarCADLink
PROJECT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SonarCADLink"
)


def load_module(name: str, *relparts: str):
    """Load a source file as an isolated module (pattern-B dedupe).

    Example::

        mod = load_module("cq", "sonarlink", "cad", "cq.py")
    """
    path = os.path.normpath(os.path.join(PROJECT_ROOT, *relparts))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install_sys_modules(mapping: dict) -> dict:
    """Install mock modules into sys.modules. Returns the saved originals."""
    saved = {k: sys.modules.get(k) for k in mapping}
    sys.modules.update(mapping)
    return saved


def restore_sys_modules(saved: dict) -> None:
    """Restore sys.modules entries saved by install_sys_modules.

    Preserves the original semantics: a key that did not exist before is
    popped, not set to None.
    """
    for key, orig in saved.items():
        if orig is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig
