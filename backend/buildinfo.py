"""Build identity: which backend code and which UI bundle is actually running.

* Release builds embed ``backend/_build_info.json`` (written by tools/build_exe.py): app
  version, source commit, source digest, architecture and tool versions. A frozen build
  never hashes itself at runtime; without embedded metadata its digest is reported as
  unknown rather than as a hash of nothing.
* Source checkouts compute ``backend_digest`` = SHA-256 over the sorted relative paths and
  bytes of ``backend/**/*.py`` once at startup.
* ``ui`` comes from ``frontend/dist/build-manifest.json`` written by the Vite build.
* ``server_instance_id`` is random per process start.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
import uuid
from pathlib import Path
from typing import Any, Optional

from . import paths

ROOT = paths.REPO_ROOT


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


def read_embedded(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    try:
        data = json.loads((path or paths.build_info_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class BuildInfo:
    def __init__(self, app_version: str, api_version: int, schema_version: int, dist: Path) -> None:
        self.app_version = app_version
        self.api_version = api_version
        self.schema_version = schema_version
        self.server_instance_id = uuid.uuid4().hex[:12]
        self.started_at_utc = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        self.dist = dist
        self.frozen = paths.is_frozen()
        # Embedded metadata describes a packaged build only; a source checkout always
        # hashes its own files, so a leftover build file can never mislabel it.
        self.embedded = read_embedded() if self.frozen else None
        digest = (self.embedded or {}).get("source_digest") if self.frozen else backend_digest()
        self.backend_digest: Optional[str] = digest if isinstance(digest, str) and len(digest) == 64 else None

    def ui(self) -> Optional[dict[str, Any]]:
        return read_ui_manifest(self.dist)  # re-read: the bundle may be rebuilt while running

    def as_dict(self) -> dict[str, Any]:
        d = self.backend_digest
        emb = self.embedded or {}
        return {
            "app_version": self.app_version,
            "api_version": self.api_version,
            "schema_version": self.schema_version,
            "backend_digest": d[:16] if d else None,
            "backend_digest_full": d,
            "build_id": f"{self.app_version}+{d[:10] if d else 'unknown'}",
            "ui": self.ui(),
            "server_instance_id": self.server_instance_id,
            "started_at_utc": self.started_at_utc,
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
            "frozen": self.frozen,
            "commit": emb.get("commit"),
            "release": {k: emb.get(k) for k in ("built_at_utc", "architecture", "pyinstaller", "python", "node", "source_dirty")} if emb else None,
        }
