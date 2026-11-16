"""Build identity: which backend code and which UI bundle is actually running.

There is no CI and no guarantee that a git checkout exists, so no commit id is claimed.
Instead:
  * ``backend_digest`` = SHA-256 over the sorted relative paths and bytes of every
    ``backend/**/*.py`` file, computed once at startup (deterministic for the same code).
  * ``ui`` comes from ``frontend/dist/build-manifest.json`` written by the Vite build
    (ui_version + ui_build_id). The same build id is compiled into the JS bundle, so a
    browser running an older bundle can notice that the server now serves a newer one.
  * ``server_instance_id`` is random per process start; it changes on every restart.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]


def backend_digest(root: Path = ROOT / "backend") -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def read_ui_manifest(dist: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads((dist / "build-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in ("ui_version", "ui_build_id", "built_at_utc")}


class BuildInfo:
    def __init__(self, app_version: str, api_version: int, schema_version: int, dist: Path) -> None:
        self.app_version = app_version
        self.api_version = api_version
        self.schema_version = schema_version
        self.server_instance_id = uuid.uuid4().hex[:12]
        self.started_at_utc = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        self.backend_digest = backend_digest()
        self.dist = dist

    def ui(self) -> Optional[dict[str, Any]]:
        return read_ui_manifest(self.dist)  # re-read: the bundle may be rebuilt while running

    def as_dict(self) -> dict[str, Any]:
        return {
            "app_version": self.app_version,
            "api_version": self.api_version,
            "schema_version": self.schema_version,
            "backend_digest": self.backend_digest[:16],
            "backend_digest_full": self.backend_digest,
            "build_id": f"{self.app_version}+{self.backend_digest[:10]}",
            "ui": self.ui(),
            "server_instance_id": self.server_instance_id,
            "started_at_utc": self.started_at_utc,
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
            "commit": None,  # not claimed: the app does not read version control
        }
