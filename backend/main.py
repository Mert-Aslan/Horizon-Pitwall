"""Single-command entry point: UDP receiver + API + UI + recorder, clean shutdown on Ctrl+C."""

from __future__ import annotations

import logging
import logging.handlers
import socket
import sys
import threading
import time
import webbrowser
from typing import Optional

import uvicorn

from . import config as config_mod
from .api.app import DIST, create_app
from .buildinfo import BuildInfo
from .recording.recorder import Recorder
from .recording.store import Store
from .telemetry import schema
from .telemetry.capture import DiagnosticCapture
from .telemetry.hub import API_VERSION, TelemetryHub
from .telemetry.receiver import UdpReceiver


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def formatTime(self, record, datefmt=None):  # noqa: N802 - logging API
        return time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created)) + f".{int(record.msecs):03d}Z"


def _setup_logging(cfg: config_mod.Config) -> None:
    root = logging.getLogger()
    root.setLevel(cfg.log_level.upper())
    # timestamp (UTC) severity component: message (events use "event=<type> key=value")
    fmt = _UtcFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        log_dir = cfg.data_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "pitwall.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        logging.getLogger("pitwall").warning("file logging disabled: %s", e)


def _web_port_free(host: str, port: int) -> Optional[str]:
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


def main(argv: Optional[list[str]] = None) -> int:
    try:
        cfg = config_mod.load(argv)
    except config_mod.ConfigError as e:
        print(f"[pitwall] configuration error in {e}", file=sys.stderr)
        return 2
    _setup_logging(cfg)
    log = logging.getLogger("pitwall")

    host = cfg.effective_web_host
    err = _web_port_free(host, cfg.web_port)
    if err:
        log.error("web port %s:%s is not available (%s). Use --web-port to pick another.", host, cfg.web_port, err)
        return 3

    build = BuildInfo(config_mod.APP_VERSION, API_VERSION, schema.SCHEMA_VERSION, DIST)
    hub = TelemetryHub(
        stale_after_s=cfg.stale_after_ms / 1000,
        equal_timestamp_policy=cfg.equal_timestamp_policy,
        server_instance_id=build.server_instance_id,
    )
    receiver = UdpReceiver(hub, cfg.udp_host, cfg.udp_port)
    recorder = None
    try:
        recorder = Recorder(Store(cfg.data_path / "recordings"), app_version=config_mod.APP_VERSION, queue_size=cfg.recorder_queue_size)
    except Exception as e:  # noqa: BLE001 - live view must work even if the data folder is unusable
        log.error("event=recorder_unavailable recordings disabled: %s", e)
    capture = DiagnosticCapture(cfg.data_path / "captures", max_bytes=int(cfg.diagnostic_capture_max_mb * 2**20), app_version=config_mod.APP_VERSION)
    app = create_app(cfg, hub, receiver, recorder=recorder, capture=capture, build=build)

    shown = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{shown}:{cfg.web_port}/"
    log.info("%s %s build %s instance %s", config_mod.APP_NAME, config_mod.APP_VERSION, build.as_dict()["build_id"], build.server_instance_id)
    log.info("UI: %s%s", url, "  (LAN access enabled, read-only for other devices)" if cfg.lan else "")
    target = "127.0.0.1 (game on this PC) or this PC's LAN address" if cfg.udp_host in ("0.0.0.0", "::") else cfg.udp_host
    log.info("In Forza Horizon 6: Settings > HUD and Gameplay > Data Out = On, IP = %s, Port = %s", target, cfg.udp_port)
    log.info("Data folder: %s", cfg.data_path)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=cfg.web_port, log_level="warning", ws="websockets-sansio", lifespan="on"))
    if cfg.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.run()
    if hub.receiver_state == "error":
        log.error("receiver error: %s", hub.receiver_error)
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
