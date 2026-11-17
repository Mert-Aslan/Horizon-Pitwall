"""Optional Windows Service mode: install, start, stop, status and uninstall.

Layout (all machine-wide, created only by an explicit ``service install``):
  %ProgramFiles%\\Horizon Pitwall\\        copy of the release folder (+ marker file)
  %ProgramData%\\HorizonPitwall\\          service root, ACL: SYSTEM + Administrators full,
                                           LOCAL SERVICE modify, nobody else
      pitwall.config.json                  service settings (never overwritten)
      data\\                               recordings, catalog, logs of the service
The service runs HorizonPitwallService.exe as ``NT AUTHORITY\\LocalService``. It never
opens a browser or tray (Session 0); the normal launcher detects it and opens its
dashboard instead of starting a second receiver.

Management needs administrator rights; a non-elevated call relaunches itself through
the UAC prompt for that one command.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from .. import config as config_mod
from .. import paths, runtime

SERVICE_NAME = "HorizonPitwall"
DISPLAY_NAME = "Horizon Pitwall"
DESCRIPTION = "Receives Forza Horizon 6 Data Out telemetry and serves the Horizon Pitwall dashboard on this PC."
SERVICE_EXE = "HorizonPitwallService.exe"
INSTALL_MARKER = ".horizon-pitwall-install"
ACCOUNT = r"NT AUTHORITY\LocalService"
# SIDs keep the ACL independent of the Windows display language.
SID_SYSTEM, SID_ADMINS, SID_LOCAL_SERVICE = "*S-1-5-18", "*S-1-5-32-544", "*S-1-5-19"


class ServiceError(RuntimeError):
    pass


# ------------------------------------------------------------------ locations
def program_data_root() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "HorizonPitwall"


def install_root() -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Horizon Pitwall"


def service_config_path(root: Optional[Path] = None) -> Path:
    return (root or program_data_root()) / "pitwall.config.json"


def load_service_config(root: Optional[Path] = None) -> config_mod.Config:
    root = root or program_data_root()
    # The service always uses its own data folder, never a user's LOCALAPPDATA.
    return config_mod.load_file(service_config_path(root), data_dir=str(root / "data"))


def service_url(root: Optional[Path] = None) -> str:
    try:
        return runtime.dashboard_url(load_service_config(root))
    except (config_mod.ConfigError, OSError):
        return runtime.dashboard_url(config_mod.Config())


# ------------------------------------------------------------------ SCM queries
STATE_NAMES = {1: "stopped", 2: "start_pending", 3: "stop_pending", 4: "running", 5: "continue_pending", 6: "pause_pending", 7: "paused"}


def state() -> Optional[str]:
    """SCM state of the Pitwall service, or None when it is not installed."""
    try:
        import pywintypes
        import win32serviceutil
    except ImportError:
        return None
    try:
        return STATE_NAMES.get(win32serviceutil.QueryServiceStatus(SERVICE_NAME)[1], "unknown")
    except pywintypes.error as e:
        if e.winerror == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
            return None
        raise


def _wait_state(target: str, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if state() == target:
            return True
        time.sleep(0.5)
    return False


# ------------------------------------------------------------------ safe file operations
def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_rmtree(target: Path, *, must_be_in: Path, marker: Optional[str] = None) -> None:
    """Delete ``target`` only if it lies strictly inside ``must_be_in`` and, when given,
    carries the installation marker. Refuses everything else."""
    t = target.resolve()
    base = must_be_in.resolve()
    if t == base or not _is_within(t, base):
        raise ServiceError(f"refusing to delete {t}: outside {base}")
    if marker is not None and not (t / marker).exists():
        raise ServiceError(f"refusing to delete {t}: not a Horizon Pitwall installation (no {marker})")
    if t.exists():
        shutil.rmtree(t)


def copy_release(src: Path, target: Path) -> None:
    """Copy the release folder to ``target`` atomically-enough: stage next to it, then
    swap. An existing target must be a previous Pitwall installation."""
    parent = target.parent
    if not (src / SERVICE_EXE).exists():
        raise ServiceError(f"{src} does not contain {SERVICE_EXE}; run this from the extracted release folder")
    if target.exists() and not (target / INSTALL_MARKER).exists():
        raise ServiceError(f"{target} exists and is not a Horizon Pitwall installation; remove it manually")
    staging = parent / (target.name + ".new")
    old = parent / (target.name + ".old")
    for p in (staging, old):
        if p.exists():
            safe_rmtree(p, must_be_in=parent, marker=INSTALL_MARKER)
    shutil.copytree(src, staging, ignore=shutil.ignore_patterns("*.log", "run"))
    (staging / INSTALL_MARKER).write_text("Horizon Pitwall service installation\n", encoding="utf-8")
    if target.exists():
        target.replace(old)
    staging.replace(target)
    if old.exists():
        safe_rmtree(old, must_be_in=parent, marker=INSTALL_MARKER)


def secure_program_data(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    r = subprocess.run(
        ["icacls", str(root), "/inheritance:r", "/grant:r",
         f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINS}:(OI)(CI)F", f"{SID_LOCAL_SERVICE}:(OI)(CI)M", "/T", "/Q"],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise ServiceError(f"could not set permissions on {root}: {r.stdout.strip() or r.stderr.strip()}")


def copy_user_data(user_data: Path, service_data: Path) -> list[str]:
    """Copy (never move) a user's recordings and car names into the service folder.
    Only into an empty destination; the originals are left untouched."""
    notes: list[str] = []
    src_rec, dst_rec = user_data / "recordings", service_data / "recordings"
    if (src_rec / "pitwall.sqlite3").exists():
        if (dst_rec / "pitwall.sqlite3").exists():
            notes.append("service already has recordings; user recordings were not copied")
        else:
            dst_rec.mkdir(parents=True, exist_ok=True)
            for d in src_rec.iterdir():
                if d.is_dir():
                    shutil.copytree(d, dst_rec / d.name)
            _sqlite_copy(src_rec / "pitwall.sqlite3", dst_rec / "pitwall.sqlite3")
            notes.append(f"copied recordings from {src_rec}")
    src_cat, dst_cat = user_data / "catalog.sqlite3", service_data / "catalog.sqlite3"
    if src_cat.exists():
        if dst_cat.exists():
            notes.append("service already has car names; user car names were not copied")
        else:
            _sqlite_copy(src_cat, dst_cat)
            notes.append(f"copied car names from {src_cat}")
    return notes


def _sqlite_copy(src: Path, dst: Path) -> None:
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


# ------------------------------------------------------------------ SCM registration
def register(exe: Path) -> None:
    import win32service

    hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        bin_path = f'"{exe}"'  # quoted: the path contains spaces
        try:
            hs = win32service.OpenService(hscm, SERVICE_NAME, win32service.SERVICE_ALL_ACCESS)
            win32service.ChangeServiceConfig(hs, win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                                             win32service.SERVICE_ERROR_NORMAL, bin_path, None, 0, None, ACCOUNT, "", DISPLAY_NAME)
        except win32service.error:
            hs = win32service.CreateService(hscm, SERVICE_NAME, DISPLAY_NAME, win32service.SERVICE_ALL_ACCESS,
                                            win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                                            win32service.SERVICE_ERROR_NORMAL, bin_path, None, 0, None, ACCOUNT, "")
        try:
            win32service.ChangeServiceConfig2(hs, win32service.SERVICE_CONFIG_DESCRIPTION, DESCRIPTION)
            # Bounded recovery: restart twice (10 s, 60 s), then stay stopped; counter resets daily.
            win32service.ChangeServiceConfig2(hs, win32service.SERVICE_CONFIG_FAILURE_ACTIONS, {
                "ResetPeriod": 86400, "RebootMsg": None, "Command": None,
                "Actions": [(win32service.SC_ACTION_RESTART, 10_000), (win32service.SC_ACTION_RESTART, 60_000), (win32service.SC_ACTION_NONE, 0)],
            })
        finally:
            win32service.CloseServiceHandle(hs)
    finally:
        win32service.CloseServiceHandle(hscm)


def unregister() -> None:
    import win32serviceutil

    win32serviceutil.RemoveService(SERVICE_NAME)


# ------------------------------------------------------------------ commands (elevated)
def cmd_install(user_data: Optional[Path]) -> str:
    if not paths.is_frozen():
        raise ServiceError("service mode needs the packaged HorizonPitwall release, not a source checkout")
    src = paths.install_dir()
    target = install_root()
    if state() in ("running", "start_pending"):
        cmd_stop()
    if src.resolve() != target.resolve():
        copy_release(src, target)
    root = program_data_root()
    secure_program_data(root)
    cfg_file = service_config_path(root)
    if not cfg_file.exists():
        cfg_file.write_text("{}\n", encoding="utf-8")  # defaults; never overwritten later
    notes = copy_user_data(user_data, root / "data") if user_data else []
    if notes:
        secure_program_data(root)  # copied files inherit the restricted ACL
    register(target / SERVICE_EXE)
    msg = cmd_start()
    return "\n".join([f"Installed to {target}", f"Service data: {root}", *notes, msg])


def cmd_start(timeout: float = 60.0) -> str:
    import win32serviceutil

    if state() is None:
        raise ServiceError("the service is not installed")
    if state() != "running":
        win32serviceutil.StartService(SERVICE_NAME)
    if not _wait_state("running", timeout):
        raise ServiceError(f"the service did not reach 'running' within {timeout:.0f} s; see {program_data_root() / 'data' / 'logs'}")
    url = service_url()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if runtime.probe(url):
            return f"Service running: {url}"
        time.sleep(0.5)
    raise ServiceError(f"the service is running but {url} does not answer; see {program_data_root() / 'data' / 'logs'}")


def cmd_stop(timeout: float = 60.0) -> str:
    import win32serviceutil

    st = state()
    if st is None:
        raise ServiceError("the service is not installed")
    if st != "stopped":
        win32serviceutil.StopService(SERVICE_NAME)
        if not _wait_state("stopped", timeout):
            raise ServiceError(f"the service did not stop within {timeout:.0f} s")
    return "Service stopped"


def cmd_uninstall(delete_data: bool) -> str:
    target = install_root()
    if target.exists() and _is_within(paths.install_dir(), target):
        # A running program cannot delete its own folder; check before changing anything.
        raise ServiceError(f"run 'service uninstall' from the extracted release folder, not from {target}")
    out = []
    if state() is not None:
        if state() != "stopped":
            out.append(cmd_stop())
        unregister()
        out.append("Service registration removed")
    if target.exists():
        safe_rmtree(target, must_be_in=target.parent, marker=INSTALL_MARKER)
        out.append(f"Removed {target}")
    root = program_data_root()
    if delete_data and root.exists():
        safe_rmtree(root, must_be_in=root.parent)
        out.append(f"Deleted service data {root}")
    elif root.exists():
        out.append(f"Service data kept in {root}")
    return "\n".join(out) or "Nothing to remove"


def cmd_status() -> str:
    st = state()
    if st is None:
        return "Service mode is not installed."
    url = service_url()
    healthy = runtime.probe(url) is not None if st == "running" else False
    return f"Service: {st}\nDashboard: {url} ({'answering' if healthy else 'not answering'})\nData: {program_data_root()}"


# ------------------------------------------------------------------ CLI
USAGE = """HorizonPitwall.exe service <command>

  install [--copy-user-data]   copy this release to Program Files, register and start the service
  start | stop | status        control the installed service
  uninstall [--delete-data]    remove the service and its program files (data kept unless --delete-data)
"""


def main(argv: list[str]) -> int:
    """Entry for ``HorizonPitwall.exe service ...``. Relaunches elevated when needed and
    reports the result in the console it was started from, or in a message box."""
    from ..launcher import winapi

    winapi.attach_parent_console()
    if not argv or argv[0] not in ("install", "start", "stop", "status", "uninstall"):
        _report(USAGE, ok=False)
        return 2
    cmd, rest = argv[0], argv[1:]
    result_file = None
    if "--result-file" in rest:
        i = rest.index("--result-file")
        result_file = Path(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    if cmd == "status":
        _report(cmd_status(), ok=True)
        return 0
    if not winapi.is_admin():
        if not paths.is_frozen():
            _report("Run this from an elevated prompt (source checkouts cannot relaunch themselves).", ok=False)
            return 1
        extra = list(rest)
        if cmd == "install" and "--copy-user-data" in extra:
            extra.remove("--copy-user-data")
            extra += ["--user-data", str(config_mod.default_data_dir())]  # this user's folder, not the admin's
        fd, tmp = tempfile.mkstemp(prefix="pitwall-service-", suffix=".txt")
        os.close(fd)
        params = subprocess.list2cmdline(["service", cmd, *extra, "--result-file", tmp])
        code = winapi.run_elevated(sys.executable, params)
        text = Path(tmp).read_text(encoding="utf-8", errors="replace") if Path(tmp).exists() else ""
        Path(tmp).unlink(missing_ok=True)
        if code is None:
            _report("The administrator prompt was cancelled; nothing was changed.", ok=False)
            return 1
        _report(text.strip() or f"exit code {code}", ok=code == 0)
        return int(code)
    try:
        if cmd == "install":
            ud = Path(rest[rest.index("--user-data") + 1]) if "--user-data" in rest else (config_mod.default_data_dir() if "--copy-user-data" in rest else None)
            text = cmd_install(ud)
        elif cmd == "start":
            text = cmd_start()
        elif cmd == "stop":
            text = cmd_stop()
        else:
            text = cmd_uninstall("--delete-data" in rest)
        ok = True
    except Exception as e:  # noqa: BLE001 - reported to the user
        text, ok = f"Service {cmd} failed: {e}", False
    if result_file is not None:
        result_file.write_text(text, encoding="utf-8")
    else:
        _report(text, ok=ok)
    return 0 if ok else 1


def _report(text: str, ok: bool) -> None:
    from ..launcher import winapi

    if sys.stdout is not None and getattr(sys.stdout, "name", "") == "CONOUT$":
        print(text, flush=True)
    else:
        winapi.message_box(text, kind="info" if ok else "error")
