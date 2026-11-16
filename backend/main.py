"""Single-command entry point: UDP receiver + API + UI, clean shutdown on Ctrl+C."""

from __future__ import annotations

import logging
import logging.handlers
import socket
import sys
import threading
import webbrowser
from typing import Optional

import uvicorn

from . import config as config_mod
from .api.app import create_app
from .telemetry.hub import TelemetryHub
from .telemetry.receiver import UdpReceiver


def _setup_logging(cfg: config_mod.Config) -> None:
    root = logging.getLogger()
    root.setLevel(cfg.log_level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    except (ValueError, FileNotFoundError) as e:
        print(f"[pitwall] configuration error: {e}", file=sys.stderr)
        return 2
    _setup_logging(cfg)
    log = logging.getLogger("pitwall")

    host = cfg.effective_web_host
    err = _web_port_free(host, cfg.web_port)
    if err:
        log.error("web port %s:%s is not available (%s). Use --web-port to pick another.", host, cfg.web_port, err)
        return 3

    hub = TelemetryHub(stale_after_s=cfg.stale_after_ms / 1000)
    receiver = UdpReceiver(hub, cfg.udp_host, cfg.udp_port)
    app = create_app(cfg, hub, receiver)

    shown = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{shown}:{cfg.web_port}/"
    log.info("%s %s", config_mod.APP_NAME, config_mod.APP_VERSION)
    log.info("UI: %s%s", url, "  (LAN access enabled)" if cfg.lan else "")
    log.info("In Forza Horizon 6: Settings > HUD and Gameplay > Data Out = On, IP = %s, Port = %s", cfg.udp_host, cfg.udp_port)

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
