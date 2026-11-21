"""Minimal logging shim for sonarlink.cad (ADR-004 §5.2: upstream core.logger equivalent).

Upstream CadAgent writes to a rotating file; the thin plugin runs inside the
FreeCAD GUI process with no file-log setup, so warnings go to Python logging
plus the FreeCAD console when available.  Same function signatures as upstream
(``log_warning(msg)`` / ``log_error(msg)``) so quality.py imports unchanged.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("sonarlink.cad")


def log_warning(msg: str) -> None:
    _log.warning(msg)
    _print_console(msg, "PrintWarning")


def log_error(msg: str) -> None:
    _log.error(msg)
    _print_console(msg, "PrintError")


def _print_console(msg: str, method: str) -> None:
    try:
        import FreeCAD  # executed inside the FreeCAD process; stub tests omit it

        getattr(FreeCAD.Console, method)(f"{msg}\n")
    except Exception:
        pass
