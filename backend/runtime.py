"""Shared application lifecycle: build, start, verify readiness and stop Pitwall.

Used by the source entry point (backend.main), the tray launcher and the Windows
service, so all three share one startup and shutdown path.

Shutdown order (uvicorn lifespan): recorder stop (flushes the open chunk, closes SQLite)
-> demo stop -> UDP receiver stop. ``stop`` asks uvicorn to exit, waits for that, then
forces exit only after a bounded timeout.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

import uvicorn

from . import config as config_mod
from .api.app import DIST, create_app
from .buildinfo import BuildInfo
from .catalog import CarCatalog
from .recording.recorder import Recorder
from .recording.store import Store
from .telemetry import schema
from .telemetry.capture import DiagnosticCapture
from .telemetry.hub import API_VERSION, TelemetryHub
from .telemetry.receiver import UdpReceiver

log = logging.getLogger("pitwall")


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def formatTime(self, record, datefmt=None):  # noqa: N802 - logging API
        return time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created)) + f".{int(record.msecs):03d}Z"


def setup_logging(cfg: config_mod.Config, console: bool = True) -> Optional[str]:
    """Rotating file log under the data folder. Returns an error text when the log file
    cannot be opened (the app keeps running without it)."""
    root = logging.getLogger()
    root.setLevel(cfg.log_level.upper())
    fmt = _UtcFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if console and sys.stderr is not None:
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        root.addHandler(h)
    try:
        log_dir = cfg.data_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "pitwall.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        return None
    except OSError as e:
        log.warning("file logging disabled: %s", e)
        return f"cannot write logs in {cfg.data_path / 'logs'}: {e.strerror or e}"


def port_free(host: str, port: int) -> Optional[str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((host, port))
        return None
    except OSError as e:
        return e.strerror or str(e)
    finally:
        s.close()


def udp_port_free(host: str, port: int) -> Optional[str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((host, port))
        return None
    except OSError as e:
        return e.strerror or str(e)
    finally:
        s.close()


def dashboard_url(cfg: config_mod.Config) -> str:
    host = cfg.effective_web_host
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{shown}:{cfg.web_port}/"


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(url: str, timeout: float = 1.5) -> Optional[dict[str, Any]]:
    """GET <url>api/v1/health. Returns the parsed body only if it is a Pitwall health
    response; any other server on the port yields None."""
    try:
        with _NO_PROXY.open(url.rstrip("/") + "/api/v1/health", timeout=timeout) as r:
            body = json.loads(r.read(65536))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(body, dict) or body.get("app") != config_mod.APP_NAME:
        return None
    if not isinstance(body.get("build"), dict) or not body["build"].get("server_instance_id"):
        return None
    return body


class PitwallRuntime:
    def __init__(self, cfg: config_mod.Config) -> None:
        self.cfg = cfg
        self.build = BuildInfo(config_mod.APP_VERSION, API_VERSION, schema.SCHEMA_VERSION, DIST)
        self.hub = TelemetryHub(
            stale_after_s=cfg.stale_after_ms / 1000,
            equal_timestamp_policy=cfg.equal_timestamp_policy,
            server_instance_id=self.build.server_instance_id,
        )
        self.receiver = UdpReceiver(self.hub, cfg.udp_host, cfg.udp_port)
        self.problems: list[str] = []
        self.recorder: Optional[Recorder] = None
        try:
            self.recorder = Recorder(Store(cfg.data_path / "recordings"), app_version=config_mod.APP_VERSION, queue_size=cfg.recorder_queue_size)
        except Exception as e:  # noqa: BLE001 - live view must work even if the data folder is unusable
            log.error("event=recorder_unavailable recordings disabled: %s", e)
            self.problems.append(f"Recording is disabled: {e}")
        self.catalog: Optional[CarCatalog] = None
        try:
            self.catalog = CarCatalog(cfg.data_path / "catalog.sqlite3")
        except Exception as e:  # noqa: BLE001
            log.error("event=catalog_unavailable car names disabled: %s", e)
            self.problems.append(f"Car names are disabled: {e}")
        capture = DiagnosticCapture(cfg.data_path / "captures", max_bytes=int(cfg.diagnostic_capture_max_mb * 2**20), app_version=config_mod.APP_VERSION)
        self.app = create_app(cfg, self.hub, self.receiver, recorder=self.recorder, capture=capture, build=self.build,
                              catalog=self.catalog, paths=cfg.paths())
        self.url = dashboard_url(cfg)
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host=cfg.effective_web_host, port=cfg.web_port, log_level="warning",
            ws="websockets-sansio", lifespan="on", log_config=None,
        ))
        self._thread: Optional[threading.Thread] = None

    @property
    def instance_id(self) -> str:
        return self.build.server_instance_id

    def log_banner(self) -> None:
        cfg = self.cfg
        log.info("%s %s build %s instance %s", config_mod.APP_NAME, config_mod.APP_VERSION, self.build.as_dict()["build_id"], self.instance_id)
        log.info("UI: %s%s", self.url, "  (LAN access enabled, read-only for other devices)" if cfg.lan else "")
        target = "127.0.0.1 (game on this PC) or this PC's LAN address" if cfg.udp_host in ("0.0.0.0", "::") else cfg.udp_host
        log.info("In Forza Horizon 6: Settings > HUD and Gameplay > Data Out = On, IP = %s, Port = %s", target, cfg.udp_port)
        log.info("Data folder: %s", cfg.data_path)

    # ------------------------------------------------------------ lifecycle
    def serve_foreground(self, on_ready: Optional[Callable[[], None]] = None, ready_timeout: float = 20.0) -> None:
        """Run in the calling (main) thread; Ctrl+C stops it gracefully."""
        if on_ready is not None:
            threading.Thread(target=self._call_when_ready, args=(on_ready, ready_timeout), name="pitwall-ready", daemon=True).start()
        self.server.run()

    def start_background(self) -> None:
        if self._thread is not None:
            raise RuntimeError("already started")
        self._thread = threading.Thread(target=self._run_logged, name="pitwall-server", daemon=False)
        self._thread.start()

    def _run_logged(self) -> None:
        try:
            self.server.run()
        except BaseException:  # noqa: BLE001 - surfaced through alive/exit state
            log.exception("event=server_crashed")

    def _call_when_ready(self, fn: Callable[[], None], timeout: float) -> None:
        if self.wait_ready(timeout):
            fn()

    @property
    def alive(self) -> bool:
        if self._thread is not None:
            return self._thread.is_alive()
        return self.server.started and not self.server.should_exit

    def wait_ready(self, timeout: float = 20.0) -> bool:
        """True once uvicorn is serving AND this very instance answers its health check
        (not merely any server on the port)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._thread is not None and not self._thread.is_alive():
                return False
            if self.server.started:
                body = probe(self.url, timeout=1.0)
                if body and body["build"]["server_instance_id"] == self.instance_id:
                    return True
            time.sleep(0.05)
        return False

    def stop(self, timeout: float = 15.0) -> bool:
        """Graceful stop; returns True when the server thread has finished."""
        self.server.should_exit = True
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        if t.is_alive():
            log.error("event=shutdown_timeout forcing exit after %.0fs", timeout)
            self.server.force_exit = True
            t.join(5)
        return not t.is_alive()
