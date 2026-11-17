"""One Pitwall per user session and data folder.

Ownership is decided by a session-local named mutex (race-free; released by the OS if
the owner dies). The owner publishes ``<data>/run/instance.json`` only after its server
answered its own health check, and removes it on exit. Other launches never trust the
file alone: the PID must be alive and ``/api/v1/health`` on the recorded URL must report
the same ``server_instance_id``. A different program on the port is never treated as
Pitwall and never stopped.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from .. import runtime
from . import winapi


def scope_key(data_dir: Path) -> str:
    return hashlib.sha256(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:16]


def mutex_name(data_dir: Path) -> str:
    return f"Local\\HorizonPitwall-{scope_key(data_dir)}"


def exit_event_name(data_dir: Path) -> str:
    return f"Local\\HorizonPitwall-{scope_key(data_dir)}-exit"


def instance_file(data_dir: Path) -> Path:
    return data_dir / "run" / "instance.json"


def write_instance(data_dir: Path, *, url: str, server_instance_id: str, exe: str) -> None:
    f = instance_file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "pid": os.getpid(), "url": url, "server_instance_id": server_instance_id, "exe": exe,
        "started_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }), encoding="utf-8")
    tmp.replace(f)


def read_instance(data_dir: Path) -> Optional[dict[str, Any]]:
    try:
        d = json.loads(instance_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("pid"), int) and isinstance(d.get("url"), str) else None


def remove_instance(data_dir: Path, server_instance_id: Optional[str] = None) -> None:
    """Remove the file, but only if it still describes this instance (or is unreadable)."""
    d = read_instance(data_dir)
    if d is None or server_instance_id is None or d.get("server_instance_id") == server_instance_id:
        try:
            instance_file(data_dir).unlink()
        except OSError:
            pass


def verify(d: Optional[dict[str, Any]]) -> Optional[str]:
    """URL of a live, healthy Pitwall described by ``d``, else None."""
    if not d or not winapi.process_alive(d["pid"]):
        return None
    url = d["url"]
    if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
        return None
    body = runtime.probe(url)
    if body and body["build"]["server_instance_id"] == d.get("server_instance_id"):
        return url
    return None


def wait_for_owner(data_dir: Path, timeout: float) -> Optional[str]:
    """Another process owns the mutex: wait (bounded) until it has published a verified
    instance, e.g. while it is still starting."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        url = verify(read_instance(data_dir))
        if url:
            return url
        time.sleep(0.2)
    return None


def request_exit(data_dir: Path, timeout: float = 30.0) -> bool:
    """Ask the owner in this session to exit (graceful) and wait until it has released
    the mutex. Returns False if no owner was running or it did not exit in time."""
    try:
        ev = winapi.NamedEvent(exit_event_name(data_dir), create=False)
    except OSError:
        return False
    try:
        ev.set()
    finally:
        ev.close()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        m = winapi.NamedMutex(mutex_name(data_dir))
        owned = m.owned
        m.release()
        if owned:
            return True
        time.sleep(0.2)
    return False
