"""Resource locations for source checkouts and frozen (PyInstaller) builds.

Read-only resources (UI bundle, build metadata) live next to the code: the repository in
a source checkout, ``sys._MEIPASS`` in a frozen build. Mutable data never goes there; it
lives in the per-user data directory (``config.default_data_dir``) or, for the Windows
service, under ProgramData.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS")) if is_frozen() else REPO_ROOT


def ui_dist() -> Path:
    override = os.environ.get("PITWALL_UI_DIST")
    return Path(override) if override else resource_root() / "frontend" / "dist"


def build_info_file() -> Path:
    return resource_root() / "backend" / "_build_info.json"


def install_dir() -> Path:
    """Folder that holds the executable (frozen) or the repository (source)."""
    return Path(sys.executable).resolve().parent if is_frozen() else REPO_ROOT
