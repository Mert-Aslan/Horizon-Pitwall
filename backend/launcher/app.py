"""HorizonPitwall.exe entry point: tray launcher for the current user.

  HorizonPitwall.exe                 start (or reuse) Pitwall and open the dashboard
  HorizonPitwall.exe --background    start in the tray without opening a browser
  HorizonPitwall.exe --exit          ask the running Pitwall of this user to exit
  HorizonPitwall.exe --paths         show where data and logs are kept
  HorizonPitwall.exe --debug-console start with a console window that shows the log
  HorizonPitwall.exe service ...     optional Windows Service mode (see docs/SERVICE.md)

Settings come from ``<data folder>\\pitwall.config.json`` (not the working directory).
Exit codes: 0 ok, 1 not started/already running but unreachable, 2 bad settings,
3 port in use, 4 dashboard files missing, 5 server did not become ready,
6 data folder not writable.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Optional

from .. import config as config_mod
from .. import paths
from . import winapi

log = logging.getLogger("pitwall.launcher")

EXIT_OK, EXIT_NOT_RUNNING, EXIT_CONFIG, EXIT_PORT, EXIT_UI, EXIT_NOT_READY, EXIT_DATA = 0, 1, 2, 3, 4, 5, 6
READY_TIMEOUT = 30.0
RESTART_LIMIT, RESTART_WINDOW = 3, 600.0


def _fix_stdio() -> None:
    """A windowed executable has no stdout/stderr; give it one (the parent console when
    started from a terminal, else a null sink) so printing can never crash it."""
    if sys.stdout is None or sys.stderr is None:
        if not winapi.attach_parent_console():
            null = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
            sys.stdout = sys.stdout or null
            sys.stderr = sys.stderr or null


def _tell(text: str, kind: str = "error") -> None:
    if getattr(sys.stdout, "name", "") == "CONOUT$" or not paths.is_frozen():
        print(text, file=sys.stderr if kind == "error" else sys.stdout, flush=True)
    else:
        winapi.message_box(text, kind=kind)


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the tray still offers "Open Dashboard"
        log.warning("could not open the browser", exc_info=True)


def _open_folder(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    os.startfile(str(p))  # noqa: S606 - explorer on our own folder


def port_owner(port: int, kind: str = "tcp") -> Optional[str]:
    """Best-effort name of the process listening on ``port`` (for the error message)."""
    try:
        import psutil

        for c in psutil.net_connections(kind=kind):
            if c.laddr and c.laddr.port == port and c.pid and (kind == "udp" or c.status == psutil.CONN_LISTEN):
                return f"{psutil.Process(c.pid).name()} (PID {c.pid})"
    except Exception:  # noqa: BLE001
        pass
    return None


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--background", action="store_true")
    ap.add_argument("--debug-console", action="store_true")
    ap.add_argument("--exit", action="store_true")
    ap.add_argument("--data-dir")
    own, rest = ap.parse_known_args(argv)
    if own.data_dir:
        rest += ["--data-dir", own.data_dir]
    return own, rest


def load_config(own: argparse.Namespace, rest: list[str]) -> config_mod.Config:
    data = Path(own.data_dir) if own.data_dir else config_mod.default_data_dir()
    return config_mod.load(rest, default_config=data / "pitwall.config.json")


class Launcher:
    def __init__(self, cfg: config_mod.Config, background: bool) -> None:
        self.cfg = cfg
        self.background = background
        self.data = cfg.data_path
        self.rt = None
        self.tray = None
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.stopped = threading.Event()
        self.restarts: deque[float] = deque()
        self.gave_up = False

    # ------------------------------------------------------------ server
    def start_server(self) -> bool:
        from .. import runtime
        from . import instance

        rt = runtime.PitwallRuntime(self.cfg)
        rt.log_banner()
        for p in rt.problems:
            log.warning("startup problem: %s", p)
        rt.start_background()
        if not rt.wait_ready(READY_TIMEOUT):
            rt.stop(timeout=10)
            return False
        with self.lock:
            self.rt = rt
        instance.write_instance(self.data, url=rt.url, server_instance_id=rt.instance_id, exe=sys.executable)
        return True

    def stop_server(self) -> None:
        from . import instance

        with self.lock:
            rt, self.rt = self.rt, None
        if rt is not None:
            rt.stop(timeout=20)
            instance.remove_instance(self.data, rt.instance_id)

    def restart(self, manual: bool = False) -> None:
        with self.lock:
            if self.stopping.is_set():
                return
            now = time.monotonic()
            while self.restarts and now - self.restarts[0] > RESTART_WINDOW:
                self.restarts.popleft()
            if not manual and len(self.restarts) >= RESTART_LIMIT:
                if not self.gave_up:
                    self.gave_up = True
                    log.error("event=restart_limit stopped restarting after %d failures in %.0f s", RESTART_LIMIT, RESTART_WINDOW)
                    self._state(False, "Horizon Pitwall stopped. Use Restart Pitwall or see the log.")
                    self._notify("Horizon Pitwall stopped after repeated failures. See Open Logs.")
                return
            self.restarts.append(now)
            self.gave_up = False
        log.warning("event=restarting manual=%s", manual)
        self.stop_server()
        if self.start_server():
            self._state(True, f"Horizon Pitwall - {self.rt.url}")
        else:
            self._state(False, "Horizon Pitwall is not responding")

    def monitor(self) -> None:
        from .. import runtime

        bad = 0
        while not self.stopping.wait(2.0):
            rt = self.rt
            if rt is None:
                if not self.gave_up:
                    self.restart()
                continue
            if not rt.alive:
                log.error("event=server_stopped_unexpectedly")
                self.restart()
                continue
            body = runtime.probe(rt.url, timeout=2.0)
            bad = 0 if body and body["build"]["server_instance_id"] == rt.instance_id else bad + 1
            if bad == 3:
                self._state(False, "Horizon Pitwall is not responding")
            elif bad == 0:
                self._state(True, f"Horizon Pitwall - {rt.url}")

    # ------------------------------------------------------------ tray glue
    def _state(self, healthy: bool, title: str) -> None:
        if self.tray is not None:
            self.tray.set_state(healthy, title)

    def _notify(self, text: str) -> None:
        if self.tray is not None:
            self.tray.notify(text)

    def open_dashboard(self) -> None:
        rt = self.rt
        _open(rt.url if rt else _url(self.cfg))

    def autostart_get(self) -> bool:
        from . import autostart

        return autostart.current() is not None

    def autostart_set(self, on: bool) -> None:
        from . import autostart

        if not paths.is_frozen():
            self._notify("Start with Windows is available in the packaged app only.")
            return
        try:
            autostart.enable(sys.executable) if on else autostart.disable()
        except OSError as e:
            self._notify(f"Could not change Start with Windows: {e}")

    def shutdown(self) -> None:
        """Idempotent; every caller returns only after the server has stopped, so the
        process never exits while recordings are still being flushed."""
        with self.lock:
            first = not self.stopping.is_set()
            self.stopping.set()
        if not first:
            self.stopped.wait(60)
            return
        try:
            log.info("event=launcher_exit")
            self.stop_server()
        finally:
            self.stopped.set()
            if self.tray is not None:
                try:
                    self.tray.stop()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------ main loop
    def run(self, exit_event) -> None:
        def watch_exit() -> None:
            while not self.stopping.is_set():
                if exit_event.wait(500):
                    log.info("event=exit_requested")
                    self.shutdown()
                    return

        threading.Thread(target=watch_exit, name="exit-event", daemon=True).start()
        threading.Thread(target=self.monitor, name="health", daemon=True).start()
        try:
            from .tray import watch_session_end

            watch_session_end(self.shutdown)
        except Exception:  # noqa: BLE001
            log.warning("session-end watcher unavailable", exc_info=True)

        if os.environ.get("PITWALL_NO_TRAY") != "1":
            try:
                from .tray import Tray

                self.tray = Tray(
                    on_open=self.open_dashboard,
                    on_data=lambda: _open_folder(self.data),
                    on_logs=lambda: _open_folder(self.data / "logs"),
                    autostart_get=self.autostart_get, autostart_set=self.autostart_set,
                    on_restart=lambda: threading.Thread(target=self.restart, kwargs={"manual": True}, daemon=True).start(),
                    can_restart=lambda: self.gave_up or self.rt is None,
                    on_exit=lambda: threading.Thread(target=self.shutdown, daemon=True).start(),
                )
                self.tray.set_state(True, f"Horizon Pitwall - {self.rt.url}")
                self.tray.run(lambda: None)
            except Exception:  # noqa: BLE001 - keep serving without a tray icon
                log.exception("tray icon unavailable; use 'HorizonPitwall.exe --exit' to stop")
                self.tray = None
        self.stopping.wait()
        self.shutdown()


def _url(cfg: config_mod.Config) -> str:
    from .. import runtime

    return runtime.dashboard_url(cfg)


def _exit_running(data: Path) -> int:
    from . import instance

    if instance.request_exit(data, timeout=60):
        _tell("Horizon Pitwall has exited.", kind="info")
        return EXIT_OK
    _tell("Horizon Pitwall is not running for this user.", kind="info")
    return EXIT_NOT_RUNNING


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _fix_stdio()
    if argv and argv[0] == "service":
        from ..service import manage

        return manage.main(argv[1:])

    own, rest = parse(argv)
    if own.debug_console and getattr(sys.stdout, "name", "") != "CONOUT$":
        winapi.alloc_console()
    try:
        cfg = load_config(own, rest)
    except config_mod.ConfigError as e:
        data = Path(own.data_dir) if own.data_dir else config_mod.default_data_dir()
        if own.exit:  # stopping must work even when the settings file is broken
            return _exit_running(data)
        _tell("Horizon Pitwall could not start because a setting is invalid:\n\n" + "\n".join(f"- {p}" for p in e.problems)
              + f"\n\nFix or delete {data / 'pitwall.config.json'} and start again.")
        return EXIT_CONFIG
    except SystemExit as e:  # argparse --help / usage errors
        return int(e.code or 0)

    if getattr(cfg, "show_paths", False):
        p = cfg.paths()
        _tell("\n".join(f"{k}: {v}" for k, v in p.items()), kind="info")
        return EXIT_OK

    data = cfg.data_path
    from . import instance

    if own.exit:
        return _exit_running(data)

    try:
        data.mkdir(parents=True, exist_ok=True)
        probe_file = data / f".write-test-{os.getpid()}"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
    except OSError as e:
        _tell(f"Horizon Pitwall cannot write to its data folder:\n{data}\n\n{e.strerror or e}")
        return EXIT_DATA

    from .. import runtime

    log_err = runtime.setup_logging(cfg, console=own.debug_console)
    if log_err:
        log.warning(log_err)

    # Service mode installed and running: use it instead of a second receiver.
    try:
        from ..service import manage

        if manage.state() == "running":
            surl = manage.service_url()
            if runtime.probe(surl):
                log.info("event=using_service url=%s", surl)
                if not own.background:
                    _open(surl)
                return EXIT_OK
    except Exception:  # noqa: BLE001 - service checks must never block the normal app
        log.debug("service check failed", exc_info=True)

    mutex = winapi.NamedMutex(instance.mutex_name(data))
    if not mutex.owned:
        url = instance.wait_for_owner(data, READY_TIMEOUT)
        mutex.release()
        if url:
            log.info("event=already_running url=%s", url)
            if not own.background:
                _open(url)
            return EXIT_OK
        _tell("Horizon Pitwall is already running for this user but does not respond.\n\n"
              "Exit it from the tray icon (or run HorizonPitwall.exe --exit) and start again.")
        return EXIT_NOT_RUNNING

    exit_event = None
    try:
        instance.remove_instance(data)  # left over from a crash; we own the mutex now
        if not (paths.ui_dist() / "index.html").exists():
            _tell(f"The dashboard files are missing:\n{paths.ui_dist()}\n\nExtract the complete release ZIP again.")
            return EXIT_UI

        host, wport, uport = cfg.effective_web_host, cfg.web_port, cfg.udp_port
        err = runtime.port_free(host, wport)
        if err:
            other = runtime.probe(f"http://127.0.0.1:{wport}/")
            who = "another Horizon Pitwall (different user or data folder)" if other else (port_owner(wport) or "another program")
            _tell(f"The dashboard port {wport} is already in use by {who}.\n\n"
                  f"Close that program, or set a different \"web_port\" in\n{data / 'pitwall.config.json'}")
            return EXIT_PORT
        err = runtime.udp_port_free(cfg.udp_host, uport)
        if err:
            who = port_owner(uport, "udp") or "another program"
            _tell(f"The telemetry port {uport} (UDP) is already in use by {who}.\n\n"
                  f"Close that program, or set a different \"udp_port\" in\n{data / 'pitwall.config.json'} "
                  "and use the same port in the game's Data Out settings.")
            return EXIT_PORT

        exit_event = winapi.NamedEvent(instance.exit_event_name(data), create=True)
        if paths.is_frozen():
            try:
                from . import autostart

                autostart.refresh_if_moved(sys.executable)
            except OSError:
                pass

        launcher = Launcher(cfg, own.background)
        if not launcher.start_server():
            _tell(f"Horizon Pitwall did not start within {READY_TIMEOUT:.0f} seconds.\n\nSee the log in\n{data / 'logs'}")
            return EXIT_NOT_READY
        log.info("event=ready url=%s", launcher.rt.url)
        if not own.background:
            _open(launcher.rt.url)
        launcher.run(exit_event)
        return EXIT_OK
    finally:
        if exit_event is not None:
            exit_event.close()
        mutex.release()
        logging.shutdown()
